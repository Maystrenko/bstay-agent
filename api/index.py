import os
import json
import urllib.parse
import random
import requests
import time
import re
from datetime import datetime, timedelta
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

groq_keys = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")
RAPID_HOST = "booking-com18.p.rapidapi.com"
STAY22_AID = "bstay24"

HOTEL_CACHE = {}
CACHE_TTL = 21600 

class ChatPayload(BaseModel):
    message: str
    lang: str = "en"
    chat_history: list = []

def clean_city_name(text):
    """Глубокая очистка запроса от лишних слов"""
    text = re.sub(r'[^\w\s]', '', text.lower()).strip()
    # Расширенный список мусора
    stop_words = [
        "отели", "отель", "гостиницы", "гостиница", "hotels", "hotel", 
        "самые", "дешевые", "дешёвые", "cheap", "cheapest", "лучшие", "best",
        "найти", "покажи", "find", "show", "в", "на", "in", "at"
    ]
    words = text.split()
    clean_words = [w for w in words if w not in stop_words]
    return " ".join(clean_words).strip()

def get_hotels_data(city_name):
    try:
        headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": RAPID_HOST}
        l_res = requests.get(f"https://{RAPID_HOST}/stays/auto-complete", headers=headers, params={"query": city_name}, timeout=6)
        l_data = l_res.json().get('data', [])
        if not l_data: return None
        dest_id = l_data[0]['id']
        
        params = {
            "locationId": dest_id, 
            "checkinDate": (datetime.now()+timedelta(days=30)).strftime('%Y-%m-%d'),
            "checkoutDate": (datetime.now()+timedelta(days=33)).strftime('%Y-%m-%d'),
            "adults": "2", "currency_code": "USD"
        }
        h_res = requests.get(f"https://{RAPID_HOST}/stays/search", headers=headers, params=params, timeout=10)
        h_json = h_res.json()
        raw = h_json.get('data', [])
        if not isinstance(raw, list): raw = h_json.get('data', {}).get('hotels', []) or h_json.get('data', {}).get('results', [])
        
        return [{"id": str(h.get('hotel_id') or h.get('id')), "name": h.get('name') or h.get('hotel_name')} for h in raw if (h.get('id') or h.get('hotel_id'))][:6]
    except: return None

def build_hotel_card(h, cat_name, btn_text):
    """Единый шаблон карточки, чтобы дизайн не пропадал"""
    link = f"https://www.stay22.com/allez/booking/{h['id']}?aid={STAY22_AID}"
    desc = h.get('d', 'Отличный вариант для вашего отдыха.')
    return f"""
    <div style='margin-top: 15px;'>
        <span style='background:#003580; color:#fff; padding:3px 10px; border-radius:20px; font-size:10px; font-weight:bold; text-transform:uppercase;'>{cat_name}</span>
        <div style='margin-top:8px; padding:12px; background:#fff; border-radius:12px; border:1px solid #eee; box-shadow:0 3px 8px rgba(0,0,0,0.03);'>
            <div style='display:flex; justify-content:space-between; align-items:center;'>
                <b style='font-size:14px;'>{h['name']}</b>
                <a href='{link}' target='_blank' style='background:#007BFF; color:#fff; text-decoration:none; padding:6px 12px; border-radius:6px; font-weight:bold; font-size:11px;'>{btn_text}</a>
            </div>
            <p style='font-size:12px; color:#666; margin:8px 0 0;'>{desc}</p>
        </div>
    </div>"""

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    current_time = time.time()
    user_lang = payload.lang if payload.lang in ["ru", "en"] else "en"
    msg = payload.message.strip()
    
    try:
        g_key = random.choice(groq_keys)
        headers = {"Authorization": f"Bearer {g_key}"}

        # 1. ОПРЕДЕЛЯЕМ ГОРОД (с глубокой очисткой)
        potential_city = clean_city_name(msg)
        if not potential_city or len(msg.split()) > 3:
            c_sys = "Extract city in English Nominative. JSON: {'c': 'London'}. If no city, 'none'."
            c_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
                json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "system", "content": c_sys}, {"role": "user", "content": msg}], "response_format": {"type": "json_object"}}, timeout=6)
            potential_city = json.loads(c_res.json()['choices'][0]['message']['content']).get("c", "none")

        if potential_city == "none":
            return JSONResponse(content={"reply": "Пожалуйста, укажите город." if user_lang == "ru" else "Specify city."})

        # 2. КЭШ
        cache_key = f"{potential_city.lower()}_{user_lang}"
        if cache_key in HOTEL_CACHE and (current_time - HOTEL_CACHE[cache_key]['timestamp'] < CACHE_TTL):
            return JSONResponse(content={"reply": HOTEL_CACHE[cache_key]['html']})

        # 3. API ОТЕЛИ
        hotels = get_hotels_data(potential_city)
        if not hotels:
            return JSONResponse(content={"reply": f"Отели в {potential_city.capitalize()} не найдены."})

        # 4. ГЕНЕРАЦИЯ ГИДА
        btn = "Book" if user_lang == "en" else "Забронировать"
        try:
            lang_name = "Russian" if user_lang == "ru" else "English"
            g_prompt = f"Create Top-3 guide for {potential_city} in {lang_name}. Use: {json.dumps(hotels)}. JSON: {{'i': 'intro', 'cats': [ {{'n': 'cat', 'h': {{'id': 'id', 'n': 'name', 'd': 'desc'}} }} ], 't': 'tips'}}"
            g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
                json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": g_prompt}], "response_format": {"type": "json_object"}}, timeout=10)
            g = json.loads(g_res.json()['choices'][0]['message']['content'])

            html = f"<div style='font-family: sans-serif;'><p>{g['i']}</p>"
            for cat in g['cats']:
                h_data = {"id": cat['h']['id'], "name": cat['h']['n'], "d": cat['h']['d']}
                html += build_hotel_card(h_data, cat['n'], btn)
            html += f"<p style='font-size:12px; color:#28a745; margin-top:15px;'><b>Совет:</b> {g['t']}</p>"
        
        except: # ПЛАН Б (теперь тоже красивый!)
            html = f"<div style='font-family: sans-serif;'><p>Лучшие варианты в {potential_city.capitalize()}:</p>"
            categories = ["💎 Premium", "🎨 Boutique", "💰 Value"]
            for i, h in enumerate(hotels[:3]):
                html += build_hotel_card(h, categories[i], btn)

        # Финальная кнопка
        all_link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(potential_city)}"
        html += f"<br><a href='{all_link}' target='_blank' style='display:block; text-align:center; padding:14px; background:#003580; color:#fff; text-decoration:none; border-radius:10px; font-weight:bold; font-size:13px;'>View all hotels →</a></div>"

        HOTEL_CACHE[cache_key] = {"timestamp": current_time, "html": html}
        return JSONResponse(content={"reply": html})

    except Exception:
        return JSONResponse(content={"reply": "Ошибка. Попробуйте еще раз."})
