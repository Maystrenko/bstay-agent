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

# --- САМЫЙ УМНЫЙ КОННЕКТ К REDIS ---
redis = None
try:
    url = os.environ.get("REDIS_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
    if url:
        # Если ссылка формата redis://default:password@host:port
        if "redis://" in url:
            # Магия поиска пароля и хоста
            auth_part = url.split("@")[0].replace("redis://", "")
            password = auth_part.split(":")[-1] if ":" in auth_part else auth_part
            host = url.split("@")[1].split(":")[0]
            redis = Redis(url=f"https://{host}", token=password)
            print("✅ Redis: Auth Parsed Successfully")
        else:
            redis = Redis.from_env()
except Exception as e:
    print(f"❌ Redis Error: {e}")

groq_keys = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")
STAY22_AID = "bstay24"

class ChatPayload(BaseModel):
    message: str
    lang: str = "ru"

def get_hotels(city, intent="general"):
    try:
        headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": "booking-com18.p.rapidapi.com"}
        # 1. Локация
        l_res = requests.get("https://booking-com18.p.rapidapi.com/stays/auto-complete", headers=headers, params={"query": city}, timeout=10)
        dest_id = l_res.json()['data'][0]['id']
        # 2. Отели
        params = {"locationId": dest_id, "checkinDate": (datetime.now()+timedelta(days=30)).strftime('%Y-%m-%d'), "checkoutDate": (datetime.now()+timedelta(days=33)).strftime('%Y-%m-%d'), "adults": "2", "currency_code": "USD"}
        if intent == "cheap": params["sortBy"] = "price_lowest"
        
        h_res = requests.get("https://booking-com18.p.rapidapi.com/stays/search", headers=headers, params=params, timeout=15)
        data = h_res.json().get('data', [])
        if not isinstance(data, list): data = h_res.json().get('data', {}).get('hotels', [])
        return [{"id": str(x.get('hotel_id') or x.get('id')), "name": x.get('name') or x.get('hotel_name')} for x in data if x.get('id') or x.get('hotel_id')][:6]
    except: return None

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    msg = payload.message.strip().lower()
    
    try:
        g_key = random.choice(groq_keys)
        headers = {"Authorization": f"Bearer {g_key}"}

        # --- КТО ТУТ ГОРОД? ---
        city = msg.replace("отели", "").replace("дешовые", "").replace("дешевые", "").strip()
        intent = "cheap" if "деш" in msg or "дешо" in msg else "general"
        
        if not city: return JSONResponse(content={"reply": "Напишите город."})

        # --- КЭШ ---
        db_key = f"h:{city}:{intent}:ru"
        if redis:
            try:
                cached = redis.get(db_key)
                if cached: return JSONResponse(content={"reply": cached})
            except: pass

        # --- API ---
        hotels = get_hotels(city, intent)
        if not hotels: return JSONResponse(content={"reply": f"Отели в {city} не найдены."})

        # --- ГЕНЕРАЦИЯ (Без лишних слов) ---
        g_prompt = f"JSON ONLY. Create Top-3 guide for {city} in Russian. Data: {json.dumps(hotels)}. Format: {{\"i\": \"text\", \"cats\": [ {{\"n\": \"Cat\", \"h\": {{\"id\": \"id\", \"n\": \"name\", \"d\": \"desc\"}} }} ]}}"
        g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": g_prompt}], "response_format": {"type": "json_object"}}, timeout=15)
        g = g_res.json()['choices'][0]['message']['content']
        res_data = json.loads(g)

        intro = res_data.get('i', f"Отели в {city.capitalize()}:")
        html = f"<div style='font-family:sans-serif;'><p>{intro}</p>"
        for cat in res_data['cats']:
            h = cat['h']
            link = f"https://www.stay22.com/allez/booking/{h['id']}?aid={STAY22_AID}"
            html += f"""
            <div style='margin-top:15px; padding:15px; background:#fff; border-radius:10px; border:1px solid #eee;'>
                <div style='display:flex; justify-content:space-between; align-items:center;'>
                    <b style='font-size:14px;'>{h['n']}</b>
                    <a href='{link}' target='_blank' style='background:#007BFF; color:#fff; text-decoration:none; padding:5px 12px; border-radius:5px; font-size:12px; font-weight:bold;'>Забронировать</a>
                </div>
                <p style='font-size:12px; color:#666; margin:5px 0 0;'>{h['d']}</p>
            </div>"""
        
        all_link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(city)}"
        html += f"<br><a href='{all_link}' target='_blank' style='display:block; text-align:center; padding:12px; background:#003580; color:#fff; text-decoration:none; border-radius:8px; font-weight:bold;'>Все отели {city.capitalize()} →</a></div>"

        # --- СОХРАНЕНИЕ ---
        if redis:
            try: redis.set(db_key, html)
            except: pass

        return JSONResponse(content={"reply": html})
    except:
        return JSONResponse(content={"reply": "Попробуйте еще раз через минуту."})
