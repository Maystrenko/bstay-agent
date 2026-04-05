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
    """Очистка и базовый перевод для стабильности"""
    text = re.sub(r'[^\w\s]', '', text.lower()).strip()
    # Быстрая замена падежей для популярных городов
    replacements = {
        "киева": "киев", "лондона": "лондон", "парижа": "париж",
        "отели": "", "отель": "", "гостиницы": "", "гостиница": "", "hotels": "", "hotel": ""
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    return text.strip()

def get_hotels_data(city_name):
    try:
        headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": RAPID_HOST}
        # 1. Быстрый автокомплит (timeout 5 сек)
        l_res = requests.get(f"https://{RAPID_HOST}/stays/auto-complete", headers=headers, params={"query": city_name}, timeout=5)
        l_data = l_res.json().get('data', [])
        if not l_data: return None
        dest_id = l_data[0]['id']
        
        # 2. Поиск (сократили количество до 6 для скорости)
        params = {
            "locationId": dest_id, 
            "checkinDate": (datetime.now()+timedelta(days=30)).strftime('%Y-%m-%d'),
            "checkoutDate": (datetime.now()+timedelta(days=33)).strftime('%Y-%m-%d'),
            "adults": "2", "currency_code": "USD"
        }
        h_res = requests.get(f"https://{RAPID_HOST}/stays/search", headers=headers, params=params, timeout=8)
        h_json = h_res.json()
        raw = h_json.get('data', [])
        if not isinstance(raw, list): raw = h_json.get('data', {}).get('hotels', []) or h_json.get('data', {}).get('results', [])
        
        return [{"id": str(h.get('hotel_id') or h.get('id')), "name": h.get('name') or h.get('hotel_name')} for h in raw if (h.get('id') or h.get('hotel_id'))][:6]
    except Exception as e:
        print(f"DEBUG: API Error: {e}")
        return None

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    current_time = time.time()
    user_lang = payload.lang if payload.lang in ["ru", "en"] else "en"
    msg = payload.message.strip()
    
    try:
        g_key = random.choice(groq_keys)
        headers = {"Authorization": f"Bearer {g_key}"}

        # --- ШАГ 1: ГОРОД ---
        potential_city = clean_city_name(msg)
        if len(potential_city.split()) > 3: # Если фраза слишком сложная, спросим ИИ
            c_sys = "Extract city name in English. JSON: {'c': 'Kyiv'}. If no city, 'none'."
            c_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
                json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "system", "content": c_sys}, {"role": "user", "content": msg}], "response_format": {"type": "json_object"}}, timeout=5)
            potential_city = json.loads(c_res.json()['choices'][0]['message']['content']).get("c", "none")

        if not potential_city or potential_city == "none":
            return JSONResponse(content={"reply": "Укажите город." if user_lang == "ru" else "Specify city."})

        # --- ШАГ 2: КЭШ ---
        cache_key = f"{potential_city.lower()}_{user_lang}"
        if cache_key in HOTEL_CACHE and (current_time - HOTEL_CACHE[cache_key]['timestamp'] < CACHE_TTL):
            return JSONResponse(content={"reply": HOTEL_CACHE[cache_key]['html']})

        # --- ШАГ 3: ОТЕЛИ ---
        hotels = get_hotels_data(potential_city)
        if not hotels:
            return JSONResponse(content={"reply": f"Не нашел отели в {potential_city}."})

        # --- ШАГ 4: ГЕНЕРАЦИЯ ГИДА (С ПРОВЕРКОЙ ВРЕМЕНИ) ---
        try:
            lang_name = "Russian" if user_lang == "ru" else "English"
            g_prompt = f"Create short Top-3 guide for {potential_city} in {lang_name}. Use: {json.dumps(hotels)}. JSON: {{'i': 'intro', 'cats': [ {{'n': 'cat', 'h': {{'id': 'id', 'n': 'name', 'd': 'desc'}} }} ], 't': 'tips'}}"
            g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
                json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": g_prompt}], "response_format": {"type": "json_object"}}, timeout=7)
            g = json.loads(g_res.json()['choices'][0]['message']['content'])

            # Красивая верстка
            html = f"<div><p>{g['i']}</p>"
            for cat in g['cats']:
                h = cat['h']
                link = f"https://www.stay22.com/allez/booking/{h['id']}?aid={STAY22_AID}"
                html += f"<div style='margin-top:12px; padding:10px; background:#fff; border-radius:8px; border:1px solid #eee;'><b>{h['n']}</b><br><small>{h['d']}</small><br><a href='{link}' target='_blank' style='color:#007BFF; font-weight:bold; text-decoration:none;'>Book Now</a></div>"
            html += f"<p style='font-size:12px; color:green;'>{g['t']}</p></div>"
        
        except: # ПЛАН Б: Если ИИ тормозит, выдаем просто список
            html = f"<p>Вот отели в {potential_city}:</p>"
            for h in hotels[:3]:
                link = f"https://www.stay22.com/allez/booking/{h['id']}?aid={STAY22_AID}"
                html += f"<div style='margin-bottom:8px;'><a href='{link}' target='_blank'>{h['name']}</a></div>"

        HOTEL_CACHE[cache_key] = {"timestamp": current_time, "html": html}
        return JSONResponse(content={"reply": html})

    except Exception as e:
        return JSONResponse(content={"reply": f"Ошибка связи. Попробуйте еще раз."})
