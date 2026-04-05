import os
import json
import urllib.parse
import random
import requests
import time
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
    lang: str = "en" # По умолчанию английский
    chat_history: list = []

def get_hotels_data(city_name):
    try:
        headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": RAPID_HOST}
        l_res = requests.get(f"https://{RAPID_HOST}/stays/auto-complete", headers=headers, params={"query": city_name}, timeout=8)
        l_data = l_res.json().get('data', [])
        if not l_data: return None
        dest_id = l_data[0]['id']
        
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
        
        if not raw: return None
        return [{"id": str(h.get('hotel_id') or h.get('id')), "name": h.get('name') or h.get('hotel_name')} for h in raw if (h.get('id') or h.get('hotel_id'))][:10]
    except Exception: return None

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    current_time = time.time()
    user_lang = payload.lang if payload.lang in ["ru", "en"] else "en"
    
    try:
        g_key = random.choice(groq_keys)
        headers = {"Authorization": f"Bearer {g_key}"}

        # --- 1. ВЫТАСКИВАЕМ ГОРОД (Всегда в английский London) ---
        city_extract_system = "Extract city and return ONLY English nominative name. JSON: {'c': 'London'}. If no city, 'none'."
        c_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
            json={
                "model": "llama-3.3-70b-versatile", 
                "messages": [{"role": "system", "content": city_extract_system}, {"role": "user", "content": payload.message}], 
                "response_format": {"type": "json_object"}
            }, timeout=8)
        city = json.loads(c_res.json()['choices'][0]['message']['content']).get("c", "none").strip()

        if city.lower() == "none":
            err_msg = "Please specify a city." if user_lang == "en" else "Пожалуйста, укажите город."
            return JSONResponse(content={"reply": err_msg})

        # --- 2. КЭШ (по городу и языку) ---
        cache_key = f"{city.lower()}_{user_lang}"
        if cache_key in HOTEL_CACHE and (current_time - HOTEL_CACHE[cache_key]['timestamp'] < CACHE_TTL):
            return JSONResponse(content={"reply": HOTEL_CACHE[cache_key]['html']})

        # --- 3. ПОИСК ---
        hotels = get_hotels_data(city)
        if not hotels:
            err_api = f"No hotels found in {city}." if user_lang == "en" else f"Отели в {city} не найдены."
            return JSONResponse(content={"reply": err_api})

        # --- 4. ГЕНЕРАЦИЯ ГИДА НА НУЖНОМ ЯЗЫКЕ ---
        # Здесь мы заставляем ИИ писать на языке пользователя (user_lang)
        lang_full = "Russian" if user_lang == "ru" else "English"
        g_prompt = f"Create a Top-3 hotel guide for {city} in {lang_full} language. Use list: {json.dumps(hotels)}. Categories: Luxury, Boutique, Value. JSON: {{'i': 'intro', 'cats': [ {{'n': 'cat_name', 'h': {{'id': 'id', 'n': 'name', 'd': 'description'}} }} ], 't': 'tips'}}"
        
        g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": g_prompt}], "response_format": {"type": "json_object"}}, timeout=12)
        g = json.loads(g_res.json()['choices'][0]['message']['content'])

        # --- 5. ВЕРСТКА ---
        btn_text = "Book" if user_lang == "en" else "Забронировать"
        tip_label = "Expert Tip:" if user_lang == "en" else "Совет эксперта:"
        all_hotels_label = f"All hotels in {city} →" if user_lang == "en" else f"Все отели в {city} →"

        html = f"<div style='font-family: sans-serif;'><p>{g['i']}</p>"
        for cat in g['cats']:
            h = cat['h']
            link = f"https://www.stay22.com/allez/booking/{h['id']}?aid={STAY22_AID}"
            html += f"""
            <div style='margin-top: 15px;'>
                <span style='background:#003580; color:#fff; padding:3px 10px; border-radius:20px; font-size:10px; font-weight:bold; text-transform:uppercase;'>{cat['n']}</span>
                <div style='margin-top:8px; padding:12px; background:#fff; border-radius:10px; border:1px solid #eee; box-shadow:0 3px 8px rgba(0,0,0,0.03);'>
                    <div style='display:flex; justify-content:space-between; align-items:center;'>
                        <b style='font-size:14px;'>{h['n']}</b>
                        <a href='{link}' target='_blank' style='background:#007BFF; color:#fff; text-decoration:none; padding:6px 12px; border-radius:6px; font-weight:bold; font-size:11px;'>{btn_text}</a>
                    </div>
                    <p style='font-size:12px; color:#666; margin:8px 0 0;'>{h['d']}</p>
                </div>
            </div>"""
        
        html += f"<div style='background:#f4f9ff; padding:12px; border-radius:8px; margin-top:20px; font-size:12px; border-left:3px solid #007BFF;'><b>{tip_label}</b> {g['t']}</div>"
        
        all_link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(city)}"
        html += f"<br><a href='{all_link}' target='_blank' style='display:block; text-align:center; padding:14px; background:#003580; color:#fff; text-decoration:none; border-radius:10px; font-weight:bold; font-size:13px;'>{all_hotels_label}</a></div>"

        HOTEL_CACHE[cache_key] = {"timestamp": current_time, "html": html}
        return JSONResponse(content={"reply": html})

    except Exception:
        return JSONResponse(content={"reply": "Error. Please try again." if user_lang == "en" else "Ошибка. Попробуйте еще раз."})
