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

# --- УМНОЕ ПОДКЛЮЧЕНИЕ К REDIS (САМОВЗЛОМ URL) ---
redis = None
try:
    # Пробуем все варианты имен переменных
    raw_url = os.environ.get("REDIS_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
    token = os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    
    if raw_url and raw_url.startswith("redis://"):
        # Если Vercel дал только одну строку redis://default:TOKEN@HOST:PORT
        # Мы вытаскиваем из неё TOKEN и HOST для работы через Python
        match = re.search(r"redis://default:(.*?)@(.*?):(\d+)", raw_url)
        if match:
            extracted_token = match.group(1)
            extracted_host = match.group(2)
            # Для библиотеки Upstash Python нужен HTTPS адрес
            rest_url = f"https://{extracted_host}"
            redis = Redis(url=rest_url, token=extracted_token)
            print("✅ Redis: Connected via Parsed REDIS_URL")
    elif raw_url and token:
        # Если ты прописал переменные вручную
        redis = Redis(url=raw_url, token=token)
        print("✅ Redis: Connected via Manual Env")
    else:
        # План Б: стандартный метод
        redis = Redis.from_env()
        print("✅ Redis: Connected via from_env")
except Exception as e:
    print(f"❌ Redis Connection Error: {e}")
    redis = None

# Настройки API
groq_keys = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")
RAPID_HOST = "booking-com18.p.rapidapi.com"
STAY22_AID = "bstay24"

class ChatPayload(BaseModel):
    message: str
    lang: str = "en"
    chat_history: list = []

def get_hotels_api(city, intent="general"):
    """Запрос к Booking API"""
    try:
        h = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": RAPID_HOST}
        # 1. Поиск города
        l_r = requests.get(f"https://{RAPID_HOST}/stays/auto-complete", headers=h, params={"query": city}, timeout=10)
        d_id = l_r.json()['data'][0]['id']
        
        # 2. Поиск отелей (через 30 дней)
        p = {
            "locationId": d_id, 
            "checkinDate": (datetime.now()+timedelta(days=30)).strftime('%Y-%m-%d'),
            "checkoutDate": (datetime.now()+timedelta(days=33)).strftime('%Y-%m-%d'),
            "adults": "2", "currency_code": "USD"
        }
        if intent == "cheap":
            p["sortBy"] = "price_lowest"
        
        res = requests.get(f"https://{RAPID_HOST}/stays/search", headers=h, params=p, timeout=15)
        raw = res.json().get('data', [])
        if not isinstance(raw, list):
            raw = res.json().get('data', {}).get('hotels', [])
        return [{"id": str(x.get('hotel_id') or x.get('id')), "name": x.get('name') or x.get('hotel_name')} for x in raw if x.get('id') or x.get('hotel_id')][:6]
    except:
        return None

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    user_lang = payload.lang if payload.lang in ["ru", "en"] else "en"
    msg = payload.message.strip()
    
    try:
        g_key = random.choice(groq_keys)
        headers = {"Authorization": f"Bearer {g_key}"}

        # 1. Распознаем город и тип (дешево/общее)
        c_p = "Extract city and intent (cheap/general). JSON: {'c': 'London', 't': 'cheap'}"
        c_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "system", "content": c_p}, {"role": "user", "content": msg}], "response_format": {"type": "json_object"}}, timeout=10)
        ext = json.loads(c_res.json()['choices'][0]['message']['content'])
        city, intent = ext.get("c", "none"), ext.get("t", "general")

        if city == "none":
            return JSONResponse(content={"reply": "Укажите название города."})

        # 2. ПРОВЕРКА БАЗЫ (REDIS)
        db_key = f"h:{city.lower()}:{intent}:{user_lang}"
        if redis:
            try:
                cached = redis.get(db_key)
                if cached: return JSONResponse(content={"reply": cached})
            except: pass

        # 3. ЕСЛИ НЕТ В БАЗЕ - API
        hotels = get_hotels_api(city, intent)
        if not hotels:
            return JSONResponse(content={"reply": f"Отели в {city} не найдены."})

        # 4. ГЕНЕРАЦИЯ ОТВЕТА
        l_f = "Russian" if user_lang == "ru" else "English"
        g_p = f"Create Top-3 hotel guide for {city} in {l_f}. Use: {json.dumps(hotels)}. JSON: {{'i': 'intro', 'cats': [ {{'n': 'Category', 'h': {{'id': 'id', 'n': 'name', 'd': 'desc'}} }} ]}}"
        g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": g_p}], "response_format": {"type": "json_object"}}, timeout=15)
        g = json.loads(g_res.json()['choices'][0]['message']['content'])

        # Защита от кривого вступления
        intro = g.get('i', '')
        if len(intro) < 10 or ".intro" in intro.lower():
            intro = f"Вот отличные отели в {city.capitalize()}:"

        btn = "Забронировать" if user_lang == "ru" else "Book"
        html = f"<div style='font-family:Karla,sans-serif;'><p>{intro}</p>"
        for cat in g['cats']:
            h = cat['h']
            link = f"https://www.stay22.com/allez/booking/{h['id']}?aid={STAY22_AID}"
            html += f"""
            <div style='margin-top:20px;'>
                <span style='background:#003580; color:#fff; padding:4px 12px; border-radius:20px; font-size:11px; font-weight:bold;'>{cat['n']}</span>
                <div style='margin-top:10px; padding:15px; background:#fff; border-radius:12px; border:1px solid #eee; box-shadow:0 4px 12px rgba(0,0,0,0.03);'>
                    <div style='display:flex; justify-content:space-between; align-items:center;'>
                        <b style='font-size:15px;'>{h['n']}</b>
                        <a href='{link}' target='_blank' style='background:#007BFF; color:#fff; text-decoration:none; padding:8px 18px; border-radius:8px; font-weight:bold; font-size:13px;'>{btn}</a>
                    </div>
                    <p style='font-size:13px; color:#666; margin:10px 0 0;'>{h['d']}</p>
                </div>
            </div>"""
        
        all_link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(city)}"
        html += f"<br><a href='{all_link}' target='_blank' style='display:block; text-align:center; padding:15px; background:#003580; color:#fff; text-decoration:none; border-radius:10px; font-weight:bold;'>Смотреть все →</a></div>"

        # 5. СОХРАНЕНИЕ В БАЗУ
        if redis:
            try: redis.set(db_key, html)
            except: pass

        return JSONResponse(content={"reply": html})
    except:
        return JSONResponse(content={"reply": "Ошибка. Попробуйте еще раз."})
