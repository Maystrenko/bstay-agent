import os
import json
import urllib.parse
import random
import requests
from datetime import datetime, timedelta
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# Ключи
groq_keys = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")
RAPID_HOST = "booking-com18.p.rapidapi.com"
STAY22_AID = "bstay24"

class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list
    lang: str = "en"

def get_hotels_data(city_name):
    """Безопасный поиск отелей"""
    try:
        headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": RAPID_HOST}
        # Поиск локации
        l_res = requests.get(f"https://{RAPID_HOST}/stays/auto-complete", headers=headers, params={"query": city_name}, timeout=7)
        l_data = l_res.json()
        l_list = l_data if isinstance(l_data, list) else l_data.get('data', [])
        if not l_list: return None
        
        # Поиск отелей
        in_d = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
        out_d = (datetime.now() + timedelta(days=33)).strftime('%Y-%m-%d')
        params = {"locationId": l_list[0].get('id'), "checkinDate": in_d, "checkoutDate": out_d, "adults": "2", "rooms": "1", "currency_code": "USD"}
        
        h_res = requests.get(f"https://{RAPID_HOST}/stays/search", headers=headers, params=params, timeout=12)
        h_data = h_res.json()
        
        # Парсим список
        raw = h_data if isinstance(h_data, list) else h_data.get('data', [])
        if not isinstance(raw, list): raw = h_data.get('data', {}).get('hotels', [])
        
        return [{"id": str(h.get('hotel_id') or h.get('id')), "name": h.get('name') or h.get('hotel_name')} for h in raw if h.get('id') or h.get('hotel_id')][:10]
    except Exception as e:
        print(f"DEBUG HOTEL ERR: {e}")
        return None

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    # ГАРАНТИРУЕМ, что ответ всегда будет словарем с ключом "reply"
    final_reply = "Извините, я не смог обработать ваш запрос."
    
    try:
        if not groq_keys: return JSONResponse(content={"reply": "Ошибка: Ключи Groq не настроены."})
        g_key = random.choice(groq_keys)

        # 1. Извлекаем город
        c_res = requests.post("https://api.groq.com/openai/v1/chat/completions", 
            headers={"Authorization": f"Bearer {g_key}"}, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": f"Extract city in English from: '{payload.message}'. JSON: {{\"city\": \"Name\"}}"}], "response_format": {"type": "json_object"}}, timeout=10)
        city = json.loads(c_res.json()['choices'][0]['message']['content']).get("city", "none")

        if city.lower() == "none":
            return JSONResponse(content={"reply": "В каком городе ищем отель?"})

        # 2. Отели
        hotels = get_hotels_data(city)
        if not hotels:
            return JSONResponse(content={"reply": f"Не нашел живых отелей в {city}. Попробуйте другой город."})

        # 3. Гид
        g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", 
            headers={"Authorization": f"Bearer {g_key}"}, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": f"Create guide for {city} based on: {json.dumps(hotels)}. JSON: {{\"intro\": \"...\", \"categories\": [{{ \"name\": \"...\", \"hotels\": [{{ \"name\": \"...\", \"id\": \"...\", \"desc\": \"...\" }}] }}], \"tips\": \"...\"}}"}], "response_format": {"type": "json_object"}}, timeout=20)
        
        g_json = json.loads(g_res.json()['choices'][0]['message']['content'])

        # 4. Верстка
        html = f"<div style='line-height:1.5;'><p>{g_json.get('intro','')}</p>"
        for cat in g_json.get('categories', []):
            html += f"<h4 style='color:#003580; margin:15px 0 5px;'>{cat['name']}</h4>"
            for h in cat.get('hotels', []):
                link = f"https://www.stay22.com/allez/booking/{h['id']}?aid={STAY22_AID}"
                html += f"<div style='margin-bottom:10px; padding:10px; background:#f9f9f9; border-radius:8px; border:1px solid #eee; display:flex; justify-content:space-between; align-items:center;'><div style='font-size:14px;'><b>{h['name']}</b><br><small style='color:#666;'>{h['desc']}</small></div><a href='{link}' target='_blank' style='background:#007BFF; color:#fff; text-decoration:none; padding:5px 12px; border-radius:6px; font-size:12px; font-weight:bold;'>Book</a></div>"
        html += f"<p style='font-size:12px; opacity:0.8;'><b>Совет:</b> {g_json.get('tips','')}</p></div>"
        
        return JSONResponse(content={"reply": html})

    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        return JSONResponse(content={"reply": f"Системная ошибка: {str(e)[:100]}"})
