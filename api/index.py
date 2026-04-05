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
    lang: str = "ru"
    chat_history: list = []

def get_hotels(city):
    try:
        headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": RAPID_HOST}
        l_res = requests.get(f"https://{RAPID_HOST}/stays/auto-complete", headers=headers, params={"query": city}, timeout=6)
        dest_id = l_res.json()['data'][0]['id']
        
        h_res = requests.get(f"https://{RAPID_HOST}/stays/search", headers=headers, 
            params={"locationId": dest_id, "checkinDate": (datetime.now()+timedelta(days=30)).strftime('%Y-%m-%d'), "checkoutDate": (datetime.now()+timedelta(days=33)).strftime('%Y-%m-%d'), "currency_code": "USD"}, timeout=10)
        data = h_res.json().get('data', [])
        if not isinstance(data, list): data = data.get('hotels', []) or data.get('results', [])
        return [{"id": str(h.get('hotel_id') or h.get('id')), "name": h.get('name') or h.get('hotel_name')} for h in data if h.get('id') or h.get('hotel_id')][:10]
    except: return None

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    current_time = time.time()
    try:
        g_key = random.choice(groq_keys)
        headers = {"Authorization": f"Bearer {g_key}"}

        # --- ИСПРАВЛЕННАЯ ЛОГИКА ОПРЕДЕЛЕНИЯ ГОРОДА ---
        # Ставим системную инструкцию ПЕРВОЙ
        messages_for_city = [{"role": "system", "content": "Extract ONLY city name in English. JSON: {'c': 'CityName'}. If no city mentioned, return 'none'."}]
        # Добавляем историю
        messages_for_city.extend(payload.chat_history[-3:])
        # Добавляем текущее сообщение
        messages_for_city.append({"role": "user", "content": payload.message})

        c_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
            json={"model": "llama-3.3-70b-versatile", "messages": messages_for_city, "response_format": {"type": "json_object"}}, timeout=7)
        
        city = json.loads(c_res.json()['choices'][0]['message']['content']).get("c", "none").strip().lower()

        # Если ИИ все равно тупит, пробуем вытащить город напрямую из последнего сообщения
        if city == "none" or len(city) < 2:
            return JSONResponse(content={"reply": "О каком городе идет речь? Напишите, например: Лондон"})

        # Кэш
        cache_key = f"{city}_{payload.lang}"
        if cache_key in HOTEL_CACHE and (current_time - HOTEL_CACHE[cache_key]['timestamp'] < CACHE_TTL):
            return JSONResponse(content={"reply": HOTEL_CACHE[cache_key]['html']})

        # Поиск отелей
        hotels = get_hotels(city)
        if not hotels:
            return JSONResponse(content={"reply": f"Не нашел отелей в {city.capitalize()}. Попробуйте другой город."})

        # Генерация гида (Топ-3)
        g_prompt = f"Create Top-3 hotel guide for {city} in Russian. Use: {json.dumps(hotels)}. JSON: {{\"i\": \"intro\", \"cats\": [ {{\"n\": \"category\", \"h\": {{\"id\": \"id\", \"n\": \"name\", \"d\": \"desc\"}} }} ], \"t\": \"tips\"}}"
        g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": g_prompt}], "response_format": {"type": "json_object"}}, timeout=12)
        g = json.loads(g_res.json()['choices'][0]['message']['content'])

        # Верстка
        html = f"<div style='font-family: Karla, sans-serif;'><p>{g['i']}</p>"
        for cat in g['cats']:
            h = cat['h']
            link = f"https://www.stay22.com/allez/booking/{h['id']}?aid={STAY22_AID}"
            html += f"<div style='margin-top:20px;'><span style='background:#003580; color:#fff; padding:4px 12px; border-radius:20px; font-size:11px; font-weight:bold;'>{cat['n']}</span><div style='margin-top:10px; padding:15px; background:#fff; border-radius:12px; border:1px solid #eee; box-shadow:0 4px 12px rgba(0,0,0,0.03);'><div style='display:flex; justify-content:space-between; align-items:center;'><span style='font-weight:bold; font-size:15px;'>{h['n']}</span><a href='{link}' target='_blank' style='background:#007BFF; color:#fff; text-decoration:none; padding:8px 18px; border-radius:8px; font-weight:bold; font-size:13px;'>Book</a></div><p style='font-size:13px; color:#666; margin:10px 0 0;'>{h['d']}</p></div></div>"
        html += f"<div style='background:#eef5ff; border-left:4px solid #007BFF; padding:15px; border-radius:8px; margin-top:25px; font-size:13px;'><b>💡 Лайфхак:</b> {g['t']}</div></div>"

        HOTEL_CACHE[cache_key] = {"timestamp": current_time, "html": html}
        return JSONResponse(content={"reply": html})
    except Exception as e:
        return JSONResponse(content={"reply": f"Ошибка: {str(e)[:50]}"})
