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

# --- ПОДКЛЮЧЕНИЕ К REDIS (УНИВЕРСАЛЬНОЕ) ---
redis = None
try:
    url = os.environ.get("REDIS_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
    # Если ссылка от Vercel (redis://), переделываем её в формат для Python (https://)
    if url and url.startswith("redis://"):
        match = re.search(r"redis://default:(.*?)@(.*?):(\d+)", url)
        if match:
            redis = Redis(url=f"https://{match.group(2)}", token=match.group(1))
            print("✅ Redis: Connected via Parsed URL")
    elif url:
        redis = Redis.from_env()
        print("✅ Redis: Connected via Env")
except Exception as e:
    print(f"❌ Redis Error: {e}")

groq_keys = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")
RAPID_HOST = "booking-com18.p.rapidapi.com"
STAY22_AID = "bstay24"

class ChatPayload(BaseModel):
    message: str
    lang: str = "ru"

def get_hotels(city, intent="general"):
    try:
        h = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": RAPID_HOST}
        l_res = requests.get(f"https://{RAPID_HOST}/stays/auto-complete", headers=h, params={"query": city}, timeout=10)
        d_id = l_res.json()['data'][0]['id']
        
        p = {"locationId": d_id, "checkinDate": (datetime.now()+timedelta(days=30)).strftime('%Y-%m-%d'), "checkoutDate": (datetime.now()+timedelta(days=33)).strftime('%Y-%m-%d'), "adults": "2", "currency_code": "USD"}
        if intent == "cheap": p["sortBy"] = "price_lowest"
        
        res = requests.get(f"https://{RAPID_HOST}/stays/search", headers=h, params=p, timeout=15)
        raw = res.json().get('data', [])
        if not isinstance(raw, list): raw = res.json().get('data', {}).get('hotels', [])
        return [{"id": str(x.get('hotel_id') or x.get('id')), "name": x.get('name') or x.get('hotel_name')} for x in raw if x.get('id') or x.get('hotel_id')][:6]
    except: return None

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    msg = payload.message.strip()
    user_lang = payload.lang
    
    try:
        g_key = random.choice(groq_keys)
        headers = {"Authorization": f"Bearer {g_key}"}

        # --- ШАГ 1: ОПРЕДЕЛЯЕМ ГОРОД ---
        # Если в сообщении всего одно слово - считаем его городом без ИИ
        if len(msg.split()) == 1:
            city, intent = msg, "general"
        else:
            c_p = f"Extract city and intent (cheap/general) from: '{msg}'. Respond ONLY JSON: {{\"c\": \"City\", \"t\": \"general\"}}"
            c_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
                json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": c_p}], "response_format": {"type": "json_object"}}, timeout=10)
            ext = json.loads(c_res.json()['choices'][0]['message']['content'])
            city, intent = ext.get("c", "none"), ext.get("t", "general")

        if city == "none" or len(city) < 2:
            return JSONResponse(content={"reply": "Пожалуйста, введите название города."})

        # --- ШАГ 2: БАЗА ДАННЫХ ---
        db_key = f"h:{city.lower()}:{intent}:{user_lang}"
        if redis:
            try:
                cached = redis.get(db_key)
                if cached: return JSONResponse(content={"reply": cached})
            except: pass

        # --- ШАГ 3: API И ГЕНЕРАЦИЯ ---
        hotels = get_hotels(city, intent)
        if not hotels: return JSONResponse(content={"reply": f"Отели в {city} не найдены."})

        l_f = "Russian" if user_lang == "ru" else "English"
        g_p = f"Top-3 hotels in {city} in {l_f}. Data: {json.dumps(hotels)}. JSON: {{\"i\": \"intro\", \"cats\": [ {{\"n\": \"Category\", \"h\": {{\"id\": \"id\", \"n\": \"name\", \"d\": \"desc\"}} }} ]}}"
        g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": g_p}], "response_format": {"type": "json_object"}}, timeout=15)
        g = json.loads(g_res.json()['choices'][0]['message']['content'])

        intro = g.get('i', f"Лучшие предложения в {city}:")
        btn = "Забронировать" if user_lang == "ru" else "Book"
        
        html = f"<div style='font-family:sans-serif;'><p>{intro}</p>"
        for cat in g['cats']:
            h = cat['h']
            link = f"https://www.stay22.com/allez/booking/{h['id']}?aid={STAY22_AID}"
            html += f"""
            <div style='margin-top:15px; padding:15px; background:#fff; border-radius:10px; border:1px solid #eee;'>
                <div style='display:flex; justify-content:space-between; align-items:center;'>
                    <b style='font-size:14px;'>{h['n']}</b>
                    <a href='{link}' target='_blank' style='background:#007BFF; color:#fff; text-decoration:none; padding:5px 12px; border-radius:5px; font-size:12px;'>{btn}</a>
                </div>
                <p style='font-size:12px; color:#666; margin:5px 0 0;'>{h['d']}</p>
            </div>"""
        
        all_link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(city)}"
        html += f"<br><a href='{all_link}' target='_blank' style='display:block; text-align:center; padding:10px; background:#003580; color:#fff; text-decoration:none; border-radius:5px;'>Все отели {city} →</a></div>"

        if redis:
            try: redis.set(db_key, html)
            except: pass

        return JSONResponse(content={"reply": html})
    except:
        return JSONResponse(content={"reply": "Ошибка. Попробуйте еще раз."})
