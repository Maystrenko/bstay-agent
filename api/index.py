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

# Настройки ключей
groq_keys = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")
RAPID_HOST = "booking-com18.p.rapidapi.com"
STAY22_AID = "bstay24"

# --- ЛОГИКА КЭШИРОВАНИЯ ---
HOTEL_CACHE = {}
CACHE_TTL = 21600  # Время жизни кэша в секундах (6 часов)

class ChatPayload(BaseModel):
    message: str
    lang: str = "ru"

def get_hotels(city):
    """Поиск отелей через RapidAPI"""
    try:
        headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": RAPID_HOST}
        l_res = requests.get(f"https://{RAPID_HOST}/stays/auto-complete", headers=headers, params={"query": city}, timeout=6)
        dest_id = l_res.json()['data'][0]['id']
        
        params = {
            "locationId": dest_id, 
            "checkinDate": (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d'),
            "checkoutDate": (datetime.now() + timedelta(days=33)).strftime('%Y-%m-%d'),
            "adults": "2", "currency_code": "USD"
        }
        h_res = requests.get(f"https://{RAPID_HOST}/stays/search", headers=headers, params=params, timeout=10)
        data = h_res.json().get('data', [])
        if not isinstance(data, list): data = data.get('hotels', []) or data.get('results', [])
        return [{"id": str(h.get('hotel_id') or h.get('id')), "name": h.get('name') or h.get('hotel_name')} for h in data if h.get('id') or h.get('hotel_id')][:10]
    except: return None

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    current_time = time.time()
    user_lang = payload.lang or "ru"
    
    try:
        g_key = random.choice(groq_keys)
        
        # 1. Извлекаем название города (нормализуем его)
        c_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {g_key}"}, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": f"Extract city in English from: '{payload.message}'. JSON: {{\"c\": \"Name\"}}"}], "response_format": {"type": "json_object"}}, timeout=5)
        city = json.loads(c_res.json()['choices'][0]['message']['content']).get("c", "none").strip().lower()

        if city == "none":
            return JSONResponse(content={"reply": "Уточните название города, пожалуйста."})

        # --- ПРОВЕРКА КЭША ---
        cache_key = f"{city}_{user_lang}"
        if cache_key in HOTEL_CACHE:
            cached_data = HOTEL_CACHE[cache_key]
            # Если кэш еще свежий — отдаем его мгновенно
            if current_time - cached_data['timestamp'] < CACHE_TTL:
                print(f"CACHE HIT: {cache_key}")
                return JSONResponse(content={"reply": cached_data['html']})

        # 2. Если в кэше пусто или он просрочен — идем в API
        hotels = get_hotels(city)
        if not hotels:
            return JSONResponse(content={"reply": f"К сожалению, не удалось найти отели в {city.capitalize()}."})

        # 3. Генерируем ГИД (Топ-3 как договаривались)
        g_prompt = f"""
        Создай гид по отелям {city} на языке: {user_lang}. 
        Используй список: {json.dumps(hotels)}. Выбери ровно 3 разных отеля.
        Категории: '💎 Премиум', '🎨 Бутик', '💰 Выгода'.
        Верни JSON: {{"i": "вступление", "cats": [ {{"n": "категория", "h": {{"id": "id", "n": "имя", "d": "описание"}} }} ], "t": "совет"}}
        """
        
        g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {g_key}"}, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": g_prompt}], "response_format": {"type": "json_object"}}, timeout=12)
        g = json.loads(g_res.json()['choices'][0]['message']['content'])

        # Собираем КРАСИВЫЙ HTML
        html = f"<div style='font-family: Karla, sans-serif;'>"
        html += f"<p style='margin-bottom: 20px;'>{g['i']}</p>"

        for cat in g['cats']:
            h = cat['h']
            link = f"https://www.stay22.com/allez/booking/{h['id']}?aid={STAY22_AID}"
            html += f"""
            <div style='margin-top: 20px;'>
                <span style='background:#003580; color:#fff; padding:4px 12px; border-radius:20px; font-size:11px; font-weight:bold;'>{cat['n']}</span>
                <div style='margin-top:10px; padding:15px; background:#fff; border-radius:12px; border:1px solid #eee; box-shadow:0 4px 12px rgba(0,0,0,0.03);'>
                    <div style='display:flex; justify-content:space-between; align-items:center;'>
                        <span style='font-weight:bold; font-size:15px;'>{h['n']}</span>
                        <a href='{link}' target='_blank' style='background:#007BFF; color:#fff; text-decoration:none; padding:8px 18px; border-radius:8px; font-weight:bold; font-size:13px;'>Book</a>
                    </div>
                    <p style='font-size:13px; color:#666; margin:10px 0 0;'>{h['d']}</p>
                </div>
            </div>"""

        html += f"<div style='background:#eef5ff; border-left:4px solid #007BFF; padding:15px; border-radius:8px; margin-top:25px; font-size:13px;'><b>💡 Лайфхак:</b> {g['t']}</div>"
        
        all_link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(city)}"
        html += f"<br><a href='{all_link}' target='_blank' style='display:block; text-align:center; padding:16px; background:#003580; color:white; text-decoration:none; border-radius:10px; font-weight:bold;'>Смотреть всё в {city.capitalize()} →</a></div>"

        # --- СОХРАНЯЕМ В КЭШ ---
        HOTEL_CACHE[cache_key] = {
            "timestamp": current_time,
            "html": html
        }

        return JSONResponse(content={"reply": html})
    
    except Exception as e:
        return JSONResponse(content={"reply": "Не удалось обновить подборку. Попробуйте еще раз."})
