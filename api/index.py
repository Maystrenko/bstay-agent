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

# --- УМНОЕ ПОДКЛЮЧЕНИЕ (САМОВЗЛОМ ССЫЛКИ VERCEL) ---
redis = None
try:
    # Берем ту самую REDIS_URL, которую мы видим на твоих скринах
    raw_url = os.environ.get("REDIS_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
    
    if raw_url and "redis://" in raw_url:
        # Vercel дает ссылку redis://default:TOKEN@HOST:PORT
        # Мы вырезаем TOKEN и HOST вручную для Python библиотеки
        parts = raw_url.replace("redis://", "").split("@")
        auth = parts[0].split(":")
        token = auth[1] if len(auth) > 1 else auth[0]
        host = parts[1].split(":")[0]
        
        # Подключаемся через HTTPS (как того требует Upstash-Redis для Python)
        redis = Redis(url=f"https://{host}", token=token)
        print("✅ Redis Connected Successfully")
    elif raw_url:
        redis = Redis.from_env()
except Exception as e:
    print(f"❌ Redis Connection Error: {e}")

# Конфиг
groq_keys = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")
STAY22_AID = "bstay24"

class ChatPayload(BaseModel):
    message: str
    lang: str = "ru"

def get_hotels(city, intent="general"):
    try:
        h = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": "booking-com18.p.rapidapi.com"}
        l_res = requests.get("https://booking-com18.p.rapidapi.com/stays/auto-complete", headers=h, params={"query": city}, timeout=10)
        d_id = l_res.json()['data'][0]['id']
        
        p = {"locationId": d_id, "checkinDate": (datetime.now()+timedelta(days=30)).strftime('%Y-%m-%d'), "checkoutDate": (datetime.now()+timedelta(days=33)).strftime('%Y-%m-%d'), "adults": "2", "currency_code": "USD"}
        if intent == "cheap": p["sortBy"] = "price_lowest"
        
        res = requests.get("https://booking-com18.p.rapidapi.com/stays/search", headers=h, params=p, timeout=15)
        raw = res.json().get('data', [])
        if not isinstance(raw, list): raw = res.json().get('data', {}).get('hotels', [])
        return [{"id": str(x.get('hotel_id') or x.get('id')), "name": x.get('name') or x.get('hotel_name')} for x in raw if x.get('id') or x.get('hotel_id')][:6]
    except: return None

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    msg = payload.message.strip().lower()
    
    try:
        g_key = random.choice(groq_keys)
        headers = {"Authorization": f"Bearer {g_key}"}

        # 1. Город и Интент (кто ты, воин?)
        city = msg.replace("отели", "").replace("дешевые", "").replace("дешовые", "").strip()
        intent = "cheap" if "деш" in msg else "general"
        
        if not city: return JSONResponse(content={"reply": "Напишите город."})

        # 2. ПРОВЕРКА БАЗЫ
        db_key = f"h:{city}:{intent}:ru"
        if redis:
            try:
                cached = redis.get(db_key)
                if cached: return JSONResponse(content={"reply": cached})
            except: pass

        # 3. API
        hotels = get_hotels(city, intent)
        if not hotels: return JSONResponse(content={"reply": "Отели не найдены."})

        # 4. ГЕНЕРАЦИЯ HTML
        g_prompt = f"Напиши на русском краткий гид по 3 отелям в {city}. Данные: {json.dumps(hotels)}. JSON ONLY: {{'i': 'текст', 'cats': [ {{'n': 'Cat', 'h': {{'id': 'id', 'n': 'name', 'd': 'desc'}} }} ]}}"
        g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": g_prompt}], "response_format": {"type": "json_object"}}, timeout=15)
        g = json.loads(g_res.json()['choices'][0]['message']['content'])

        intro = g.get('i', f"Отели в {city.capitalize()}:")
        html = f"<div style='font-family:Karla,sans-serif;'><p>{intro}</p>"
        for cat in g['cats']:
            h = cat['h']
            link = f"https://www.stay22.com/allez/booking/{h['id']}?aid={STAY22_AID}"
            html += f"""<div style='margin-top:20px;'><span style='background:#003580; color:#fff; padding:4px 12px; border-radius:20px; font-size:11px; font-weight:bold;'>{cat['n']}</span><div style='margin-top:10px; padding:15px; background:#fff; border-radius:12px; border:1px solid #eee;'><div style='display:flex; justify-content:space-between; align-items:center;'><b>{h['n']}</b><a href='{link}' target='_blank' style='background:#007BFF; color:#fff; text-decoration:none; padding:8px 18px; border-radius:8px; font-weight:bold; font-size:13px;'>Забронировать</a></div><p style='font-size:12px; color:#666; margin:10px 0 0;'>{h['d']}</p></div></div>"""
        
        all_link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(city)}"
        html += f"<br><a href='{all_link}' target='_blank' style='display:block; text-align:center; padding:15px; background:#003580; color:#fff; text-decoration:none; border-radius:10px; font-weight:bold;'>Все отели →</a></div>"

        # 5. СОХРАНЕНИЕ В БАЗУ
        if redis:
            try: redis.set(db_key, html)
            except: pass

        return JSONResponse(content={"reply": html})
    except Exception:
        return JSONResponse(content={"reply": "Ошибка. Попробуйте еще раз."})
