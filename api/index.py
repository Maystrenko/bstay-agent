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

# --- УМНОЕ ПОДКЛЮЧЕНИЕ К REDIS (АВТО-ПАРСИНГ) ---
redis = None
try:
    raw_url = os.environ.get("REDIS_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
    if raw_url and raw_url.startswith("redis://"):
        # Извлекаем TOKEN и HOST из стандартной ссылки Vercel
        match = re.search(r"redis://default:(.*?)@(.*?):(\d+)", raw_url)
        if match:
            t_ext, h_ext = match.group(1), match.group(2)
            redis = Redis(url=f"https://{h_ext}", token=t_ext)
            print("✅ Redis: Connected via Parsed URL")
    elif os.environ.get("UPSTASH_REDIS_REST_TOKEN"):
        redis = Redis.from_env()
except Exception as e:
    print(f"❌ Redis Error: {e}")

# Конфиг
groq_keys = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")
RAPID_HOST = "booking-com18.p.rapidapi.com"
STAY22_AID = "bstay24"

class ChatPayload(BaseModel):
    message: str
    lang: str = "en"

def get_hotels_api(city, intent="general"):
    try:
        h = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": RAPID_HOST}
        l_r = requests.get(f"https://{RAPID_HOST}/stays/auto-complete", headers=h, params={"query": city}, timeout=10)
        d_id = l_r.json()['data'][0]['id']
        
        p = {"locationId": d_id, "checkinDate": (datetime.now()+timedelta(days=30)).strftime('%Y-%m-%d'), "checkoutDate": (datetime.now()+timedelta(days=33)).strftime('%Y-%m-%d'), "adults": "2", "currency_code": "USD"}
        if intent == "cheap": p["sortBy"] = "price_lowest"
        
        res = requests.get(f"https://{RAPID_HOST}/stays/search", headers=h, params=p, timeout=15)
        raw = res.json().get('data', [])
        if not isinstance(raw, list): raw = res.json().get('data', {}).get('hotels', [])
        return [{"id": str(x.get('hotel_id') or x.get('id')), "name": x.get('name') or x.get('hotel_name')} for x in raw if x.get('id') or x.get('hotel_id')][:6]
    except: return None

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    user_lang = payload.lang if payload.lang in ["ru", "en"] else "en"
    msg = payload.message.strip()
    
    try:
        g_key = random.choice(groq_keys)
        headers = {"Authorization": f"Bearer {g_key}"}

        # 1. РАСПОЗНАВАНИЕ ГОРОДА (Улучшено)
        prompt_city = f"Identify city and intent from: '{msg}'. Respond ONLY JSON: {{\"c\": \"CityName\", \"t\": \"cheap\" or \"general\"}}. If no city, \"c\": \"none\"."
        c_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt_city}], "response_format": {"type": "json_object"}}, timeout=10)
        
        data = c_res.json()['choices'][0]['message']['content']
        ext = json.loads(data)
        city, intent = ext.get("c", "none"), ext.get("t", "general")

        if city == "none" or len(city) < 2:
            return JSONResponse(content={"reply": "Напишите название города, например: 'Лондон' или 'Отели Дубая'." if user_lang == "ru" else "Please enter a city name."})

        # 2. REDIS КЭШ
        db_key = f"h:{city.lower()}:{intent}:{user_lang}"
        if redis:
            try:
                cached = redis.get(db_key)
                if cached: return JSONResponse(content={"reply": cached})
            except: pass

        # 3. API ПОИСК
        hotels = get_hotels_api(city, intent)
        if not hotels: return JSONResponse(content={"reply": f"Отели в {city} не найдены."})

        # 4. ГЕНЕРАЦИЯ HTML
        l_f = "Russian" if user_lang == "ru" else "English"
        btn = "Забронировать" if user_lang == "ru" else "Book"
        g_p = f"Write a Top-3 hotel guide for {city} in {l_f}. Use data: {json.dumps(hotels)}. Format as JSON: {{\"i\": \"intro text\", \"cats\": [ {{\"n\": \"Category\", \"h\": {{\"id\": \"id\", \"n\": \"name\", \"d\": \"short description\"}} }} ]}}"
        
        g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": g_p}], "response_format": {"type": "json_object"}}, timeout=15)
        g = json.loads(g_res.json()['choices'][0]['message']['content'])

        intro = g.get('i', f"Вот лучшие предложения в {city.capitalize()}:")
        html = f"<div style='font-family:Karla,sans-serif;'><p>{intro}</p>"
        
        for cat in g['cats']:
            h = cat['h']
            link = f"https://www.stay22.com/allez/booking/{h['id']}?aid={STAY22_AID}"
            html += f"""
            <div style='margin-top:18px;'>
                <span style='background:#003580; color:#fff; padding:3px 10px; border-radius:15px; font-size:10px; font-weight:bold;'>{cat['n']}</span>
                <div style='margin-top:8px; padding:15px; background:#fff; border-radius:10px; border:1px solid #eee; box-shadow:0 2px 8px rgba(0,0,0,0.05);'>
                    <div style='display:flex; justify-content:space-between; align-items:center;'>
                        <b style='font-size:14px;'>{h['n']}</b>
                        <a href='{link}' target='_blank' style='background:#007BFF; color:#fff; text-decoration:none; padding:7px 15px; border-radius:6px; font-weight:bold; font-size:12px;'>{btn}</a>
                    </div>
                    <p style='font-size:12px; color:#666; margin:8px 0 0;'>{h['d']}</p>
                </div>
            </div>"""
        
        all_link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(city)}"
        if intent == "cheap": all_link += "&sortby=price_lowest"
        html += f"<br><a href='{all_link}' target='_blank' style='display:block; text-align:center; padding:12px; background:#003580; color:#fff; text-decoration:none; border-radius:8px; font-weight:bold;'>Показать все варианты →</a></div>"

        # 5. СОХРАНЕНИЕ
        if redis:
            try: redis.set(db_key, html)
            except: pass

        return JSONResponse(content={"reply": html})
    except Exception as e:
        print(f"Error: {e}")
        return JSONResponse(content={"reply": "Ошибка связи. Попробуйте еще раз."})
