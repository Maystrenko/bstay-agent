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
    chat_history: list = [] # Сюда прилетит история из JS

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    current_time = time.time()
    
    try:
        g_key = random.choice(groq_keys)
        headers = {"Authorization": f"Bearer {g_key}"}

        # Собираем контекст для ИИ (последние 4 сообщения, чтобы не тормозить)
        messages_for_ai = []
        for msg in payload.chat_history[-4:]:
            messages_for_ai.append({"role": msg["role"], "content": msg["content"]})
        messages_for_ai.append({"role": "user", "content": payload.message})

        # 1. Извлекаем город с учетом контекста
        # (ИИ поймет, если юзер скажет "А там есть что-то дешевле?")
        city_prompt = f"Based on conversation, extract ONLY city name in English. If not clear, return 'none'. JSON: {{\"c\": \"Name\"}}"
        c_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
            json={"model": "llama-3.3-70b-versatile", "messages": messages_for_ai + [{"role": "system", "content": city_prompt}], "response_format": {"type": "json_object"}}, timeout=6)
        city = json.loads(c_res.json()['choices'][0]['message']['content']).get("c", "none").strip().lower()

        if city == "none":
            return JSONResponse(content={"reply": "О каком городе идет речь? Напишите название."})

        # 2. Кэш
        cache_key = f"{city}_{payload.lang}"
        if cache_key in HOTEL_CACHE and (current_time - HOTEL_CACHE[cache_key]['timestamp'] < CACHE_TTL):
            return JSONResponse(content={"reply": HOTEL_CACHE[cache_key]['html']})

        # 3. API Отелей (RapidAPI)
        h_headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": RAPID_HOST}
        l_res = requests.get(f"https://{RAPID_HOST}/stays/auto-complete", headers=h_headers, params={"query": city}, timeout=6)
        dest_id = l_res.json()['data'][0]['id']
        
        h_res = requests.get(f"https://{RAPID_HOST}/stays/search", headers=h_headers, 
            params={"locationId": dest_id, "checkinDate": (datetime.now()+timedelta(days=30)).strftime('%Y-%m-%d'), "checkoutDate": (datetime.now()+timedelta(days=33)).strftime('%Y-%m-%d'), "currency_code": "USD"}, timeout=10)
        data = h_res.json().get('data', [])
        if not isinstance(data, list): data = data.get('hotels', []) or data.get('results', [])
        hotels = [{"id": str(h.get('hotel_id') or h.get('id')), "name": h.get('name') or h.get('hotel_name')} for h in data if h.get('id') or h.get('hotel_id')][:10]

        # 4. ИИ Генерирует ответ
        g_prompt = f"Create a Top-3 hotel guide for {city} in Russian. Use: {json.dumps(hotels)}. JSON: {{\"i\": \"intro\", \"cats\": [ {{\"n\": \"category\", \"h\": {{\"id\": \"id\", \"n\": \"name\", \"d\": \"desc\"}} }} ], \"t\": \"tips\"}}"
        g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": g_prompt}], "response_format": {"type": "json_object"}}, timeout=12)
        g = json.loads(g_res.json()['choices'][0]['message']['content'])

        # Сборка HTML (та самая, красивая)
        html = f"<div style='font-family: Karla, sans-serif;'><p>{g['i']}</p>"
        for cat in g['cats']:
            h = cat['h']
            link = f"https://www.stay22.com/allez/booking/{h['id']}?aid={STAY22_AID}"
            html += f"<div style='margin-top:20px;'><span style='background:#003580; color:#fff; padding:4px 12px; border-radius:20px; font-size:11px; font-weight:bold;'>{cat['n']}</span><div style='margin-top:10px; padding:15px; background:#fff; border-radius:12px; border:1px solid #eee; box-shadow:0 4px 12px rgba(0,0,0,0.03);'><div style='display:flex; justify-content:space-between; align-items:center;'><span style='font-weight:bold; font-size:15px;'>{h['n']}</span><a href='{link}' target='_blank' style='background:#007BFF; color:#fff; text-decoration:none; padding:8px 18px; border-radius:8px; font-weight:bold; font-size:13px;'>Book</a></div><p style='font-size:13px; color:#666; margin:10px 0 0;'>{h['d']}</p></div></div>"
        
        html += f"<div style='background:#eef5ff; border-left:4px solid #007BFF; padding:15px; border-radius:8px; margin-top:25px; font-size:13px;'><b>💡 Лайфхак:</b> {g['t']}</div></div>"

        # Сохраняем в кэш
        HOTEL_CACHE[cache_key] = {"timestamp": current_time, "html": html}

        return JSONResponse(content={"reply": html})
    except:
        return JSONResponse(content={"reply": "Ошибка. Попробуйте еще раз."})
