import os
import json
import urllib.parse
import random
import requests
import re
from datetime import datetime, timedelta
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from upstash_redis import Redis

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# --- ПОДКЛЮЧЕНИЕ К REDIS ---
try:
    u = os.environ.get("UPSTASH_REDIS_REST_URL")
    t = os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    redis = Redis(url=u, token=t) if u and t else None
except:
    redis = None

groq_keys = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")
STAY22_AID = "bstay24"

class ChatPayload(BaseModel):
    message: str

def get_hotels(city_query, intent):
    """Строгий поиск: сначала находим точный ID города"""
    try:
        headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": "booking-com18.p.rapidapi.com"}
        
        # 1. Поиск ID локации (ждем максимум 5 сек)
        l_res = requests.get("https://booking-com18.p.rapidapi.com/stays/auto-complete", 
                             headers=headers, params={"query": city_query}, timeout=5)
        locs = l_res.json().get('data', [])
        if not locs: return None
        
        # Берем первый ID из списка (самый релевантный)
        dest_id = locs[0]['id']
        
        # 2. Поиск отелей именно в этой локации
        params = {
            "locationId": dest_id, 
            "checkinDate": (datetime.now()+timedelta(days=30)).strftime('%Y-%m-%d'),
            "checkoutDate": (datetime.now()+timedelta(days=33)).strftime('%Y-%m-%d'),
            "adults": "2", "currency_code": "USD"
        }
        if intent == "cheap": params["sortBy"] = "price_lowest"
        
        h_res = requests.get("https://booking-com18.p.rapidapi.com/stays/search", headers=headers, params=params, timeout=10)
        data = h_res.json().get('data', [])
        if not isinstance(data, list): data = h_res.json().get('data', {}).get('hotels', [])
        
        return [{"id": str(x.get('hotel_id') or x.get('id')), "name": x.get('name') or x.get('hotel_name')} for x in data if x.get('id') or x.get('hotel_id')][:5]
    except:
        return None

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    msg = payload.message.strip().lower()
    g_key = random.choice(groq_keys)
    headers = {"Authorization": f"Bearer {g_key}"}
    
    try:
        # --- ШАГ 1: ВЫТАЙКИВАЕМ ЧИСТЫЙ ГОРОД ЧЕРЕЗ ИИ ---
        # Это предотвращает попадание "Болгарии", если юзер написал "хочу дешево в лондон"
        p_extract = f"Extract ONLY city name in English from: '{msg}'. Respond ONLY with the city name. Example: 'London'."
        c_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": p_extract}]}, timeout=5)
        
        city = c_res.json()['choices'][0]['message']['content'].strip().replace(".", "")
        intent = "cheap" if any(x in msg for x in ["деш", "бюдж", "cheap"]) else "general"

        if len(city) < 2 or "sorry" in city.lower():
            return JSONResponse(content={"reply": "Напишите название города."})

        # --- ШАГ 2: БАЗА (REDIS) ---
        db_key = f"h:{city.lower()}:{intent}:ru"
        if redis:
            try:
                cached = redis.get(db_key)
                if cached: return JSONResponse(content={"reply": cached})
            except: pass

        # --- ШАГ 3: API И ГЕНЕРАЦИЯ ---
        hotels = get_hotels(city, intent)
        if not hotels:
            return JSONResponse(content={"reply": f"Отели в {city} не найдены. Попробуйте уточнить название."})

        # Пишем гид
        g_prompt = f"Напиши на русском гид по 3 отелям в {city}. Данные: {json.dumps(hotels)}. JSON ONLY: {{\"i\": \"текст\", \"cats\": [ {{\"n\": \"категория\", \"h\": {{\"id\": \"id\", \"n\": \"имя\", \"d\": \"описание\"}} }} ]}}"
        g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": g_prompt}], "response_format": {"type": "json_object"}}, timeout=10)
        
        res_data = json.loads(g_res.json()['choices'][0]['message']['content'])
        
        # Сборка HTML
        html = f"<div style='font-family:sans-serif;'><p>{res_data.get('i', '')}</p>"
        for cat in res_data['cats']:
            h = cat['h']
            link = f"https://www.stay22.com/allez/booking/{h['id']}?aid={STAY22_AID}"
            html += f"""
            <div style='margin-top:12px; padding:12px; background:#fff; border-radius:8px; border:1px solid #eee;'>
                <div style='display:flex; justify-content:space-between; align-items:center;'>
                    <b style='font-size:14px;'>{h['n']}</b>
                    <a href='{link}' target='_blank' style='background:#007BFF; color:#fff; text-decoration:none; padding:6px 12px; border-radius:5px; font-size:12px; font-weight:bold;'>Забронировать</a>
                </div>
                <p style='font-size:12px; color:#666; margin:6px 0 0;'>{h['d']}</p>
            </div>"""
        
        all_link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(city)}"
        html += f"<br><a href='{all_link}' target='_blank' style='display:block; text-align:center; padding:12px; background:#003580; color:#fff; text-decoration:none; border-radius:6px; font-weight:bold;'>Смотреть все в {city} →</a></div>"

        # Сохранение
        if redis:
            try: redis.set(db_key, html)
            except: pass

        return JSONResponse(content={"reply": html})
    except:
        return JSONResponse(content={"reply": "Ошибка связи. Попробуйте еще раз."})
