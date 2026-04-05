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
    """Удаляет лишние слова из короткого запроса, оставляя только город"""
    # Список слов, которые нужно вырезать
    stop_words = [
        "отели", "отель", "гостиницы", "гостиница", "hotels", "hotel", 
        "дешевые", "cheap", "найти", "find", "в", "in", "хочу", "want"
    ]
    # Убираем пунктуацию и приводим к нижнему регистру
    text = re.sub(r'[^\w\s]', '', text.lower()).strip()
    # Убираем стоп-слова
    words = text.split()
    clean_words = [w for w in words if w not in stop_words]
    return " ".join(clean_words).strip()

def get_hotels_data(city_name):
    try:
        headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": RAPID_HOST}
        # 1. Поиск ID локации
        l_res = requests.get(f"https://{RAPID_HOST}/stays/auto-complete", headers=headers, params={"query": city_name}, timeout=8)
        l_data = l_res.json().get('data', [])
        if not l_data: return None
        dest_id = l_data[0]['id']
        
        # 2. Поиск отелей
        params = {
            "locationId": dest_id, 
            "checkinDate": (datetime.now()+timedelta(days=30)).strftime('%Y-%m-%d'),
            "checkoutDate": (datetime.now()+timedelta(days=33)).strftime('%Y-%m-%d'),
            "adults": "2", "currency_code": "USD"
        }
        h_res = requests.get(f"https://{RAPID_HOST}/stays/search", headers=headers, params=params, timeout=12)
        h_json = h_res.json()
        raw = h_json.get('data', [])
        if not isinstance(raw, list): raw = h_json.get('data', {}).get('hotels', []) or h_json.get('data', {}).get('results', [])
        
        return [{"id": str(h.get('hotel_id') or h.get('id')), "name": h.get('name') or h.get('hotel_name')} for h in raw if (h.get('id') or h.get('hotel_id'))][:10]
    except Exception: return None

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    current_time = time.time()
    user_lang = payload.lang if payload.lang in ["ru", "en"] else "en"
    msg = payload.message.strip()
    
    try:
        g_key = random.choice(groq_keys)
        headers = {"Authorization": f"Bearer {g_key}"}

        # --- ШАГ 1: ВЫТАСКИВАЕМ ГОРОД ---
        words_count = len(msg.split())
        
        if words_count <= 3:
            # Если фраза короткая, чистим её от слов "отели", "дешевые" и т.д.
            potential_city = clean_city_name(msg)
        else:
            # Для длинных фраз используем ИИ
            c_sys = "Extract the city name and return it in English Nominative. JSON ONLY: {'c': 'Kyiv'}. If no city, 'none'."
            c_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
                json={
                    "model": "llama-3.3-70b-versatile", 
                    "messages": [{"role": "system", "content": c_sys}, {"role": "user", "content": msg}], 
                    "response_format": {"type": "json_object"}
                }, timeout=8)
            potential_city = json.loads(c_res.json()['choices'][0]['message']['content']).get("c", "none")

        if not potential_city or potential_city.lower() == "none":
            err = "Please specify the city." if user_lang == "en" else "Пожалуйста, укажите город."
            return JSONResponse(content={"reply": err})

        # --- ШАГ 2: КЭШ ---
        cache_key = f"{potential_city.lower()}_{user_lang}"
        if cache_key in HOTEL_CACHE and (current_time - HOTEL_CACHE[cache_key]['timestamp'] < CACHE_TTL):
            return JSONResponse(content={"reply": HOTEL_CACHE[cache_key]['html']})

        # --- ШАГ 3: API BOOKING ---
        hotels = get_hotels_data(potential_city)
        if not hotels:
            err_api = f"No hotels found in {potential_city}." if user_lang == "en" else f"Отели в {potential_city} не найдены."
            return JSONResponse(content={"reply": err_api})

        # --- ШАГ 4: ГИД ---
        lang_name = "Russian" if user_lang == "ru" else "English"
        g_prompt = f"Create a Top-3 hotel guide for {potential_city} in {lang_name}. Use: {json.dumps(hotels)}. JSON: {{'i': 'intro', 'cats': [ {{'n': 'category', 'h': {{'id': 'id', 'n': 'name', 'd': 'desc'}} }} ], 't': 'tips'}}"
        
        g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": g_prompt}], "response_format": {"type": "json_object"}}, timeout=12)
        g = json.loads(g_res.json()['choices'][0]['message']['content'])

        # --- ШАГ 5: HTML ---
        btn = "Book" if user_lang == "en" else "Забронировать"
        html = f"<div style='font-family: sans-serif;'><p>{g['i']}</p>"
        for cat in g['cats']:
            h = cat['h']
            link = f"https://www.stay22.com/allez/booking/{h['id']}?aid={STAY22_AID}"
            html += f"""
            <div style='margin-top: 15px;'>
                <span style='background:#003580; color:#fff; padding:3px 10px; border-radius:20px; font-size:10px; font-weight:bold;'>{cat['n']}</span>
                <div style='margin-top:8px; padding:12px; background:#fff; border-radius:12px; border:1px solid #eee; box-shadow:0 3px 8px rgba(0,0,0,0.03);'>
                    <div style='display:flex; justify-content:space-between; align-items:center;'>
                        <b style='font-size:14px;'>{h['n']}</b>
                        <a href='{link}' target='_blank' style='background:#007BFF; color:#fff; text-decoration:none; padding:6px 12px; border-radius:6px; font-weight:bold; font-size:11px;'>{btn}</a>
                    </div>
                    <p style='font-size:12px; color:#666; margin:8px 0 0;'>{h['d']}</p>
                </div>
            </div>"""
        
        all_link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(potential_city)}"
        html += f"<br><a href='{all_link}' target='_blank' style='display:block; text-align:center; padding:14px; background:#003580; color:#fff; text-decoration:none; border-radius:10px; font-weight:bold; font-size:13px;'>View all in {potential_city} →</a></div>"

        HOTEL_CACHE[cache_key] = {"timestamp": current_time, "html": html}
        return JSONResponse(content={"reply": html})

    except Exception:
        return JSONResponse(content={"reply": "Error. Try again later."})
