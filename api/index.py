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
redis = None
try:
    url = os.environ.get("REDIS_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
    if url and "redis://" in url:
        auth = url.replace("redis://", "").split("@")[0]
        token = auth.split(":")[-1]
        host = url.split("@")[1].split(":")[0]
        redis = Redis(url=f"https://{host}", token=token)
        print("✅ Redis Connected")
    elif url:
        redis = Redis.from_env()
except Exception as e:
    print(f"❌ Redis Error: {e}")

groq_keys = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")
STAY22_AID = "bstay24"

class ChatPayload(BaseModel):
    message: str
    lang: str = "ru"

def get_hotels(city_name, intent="general"):
    try:
        headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": "booking-com18.p.rapidapi.com"}
        # Поиск ID города (добавили ограничение поиска)
        l_res = requests.get("https://booking-com18.p.rapidapi.com/stays/auto-complete", 
                             headers=headers, params={"query": city_name}, timeout=10)
        locations = l_res.json().get('data', [])
        if not locations: return None
        
        # Берем самый первый результат, который максимально совпадает
        dest_id = locations[0]['id']
        
        params = {
            "locationId": dest_id, 
            "checkinDate": (datetime.now()+timedelta(days=30)).strftime('%Y-%m-%d'),
            "checkoutDate": (datetime.now()+timedelta(days=33)).strftime('%Y-%m-%d'),
            "adults": "2", "currency_code": "USD"
        }
        if intent == "cheap": params["sortBy"] = "price_lowest"
        
        h_res = requests.get("https://booking-com18.p.rapidapi.com/stays/search", headers=headers, params=params, timeout=15)
        h_data = h_res.json().get('data', [])
        if not isinstance(h_data, list): h_data = h_res.json().get('data', {}).get('hotels', [])
        return [{"id": str(x.get('hotel_id') or x.get('id')), "name": x.get('name') or x.get('hotel_name')} for x in h_data if x.get('id') or x.get('hotel_id')][:6]
    except: return None

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    msg = payload.message.strip()
    
    try:
        g_key = random.choice(groq_keys)
        headers = {"Authorization": f"Bearer {g_key}"}

        # --- ШАГ 1: ВЫТАЙКИВАЕМ ЧИСТЫЙ ГОРОД ЧЕРЕЗ ИИ (СТРОГО) ---
        city_prompt = f"Extract only the city name and intent (cheap/general) from this text: '{msg}'. Respond ONLY with JSON: {{\"city\": \"London\", \"intent\": \"general\"}}. Normalize city to English."
        city_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": city_prompt}], "response_format": {"type": "json_object"}}, timeout=10)
        
        city_info = json.loads(city_res.json()['choices'][0]['message']['content'])
        city = city_info.get("city", "none")
        intent = city_info.get("intent", "general")

        if city == "none" or len(city) < 2:
            return JSONResponse(content={"reply": "Укажите город для поиска."})

        # --- ШАГ 2: БАЗА (КЭШ) ---
        db_key = f"h:{city.lower()}:{intent}:ru"
        if redis:
            try:
                cached = redis.get(db_key)
                if cached: return JSONResponse(content={"reply": cached})
            except: pass

        # --- ШАГ 3: ПОИСК И ГЕНЕРАЦИЯ ---
        hotels = get_hotels(city, intent)
        if not hotels: return JSONResponse(content={"reply": f"Отели в {city} не найдены."})

        g_prompt = f"Напиши гид по 3 отелям в {city} на русском. Данные: {json.dumps(hotels)}. JSON ONLY: {{\"i\": \"текст\", \"cats\": [ {{\"n\": \"категория\", \"h\": {{\"id\": \"id\", \"n\": \"имя\", \"d\": \"описание\"}} }} ]}}"
        g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": g_prompt}], "response_format": {"type": "json_object"}}, timeout=15)
        res_json = json.loads(g_res.json()['choices'][0]['message']['content'])

        html = f"<div style='font-family:sans-serif;'><p>{res_json.get('i', '')}</p>"
        for cat in res_json['cats']:
            h = cat['h']
            link = f"https://www.stay22.com/allez/booking/{h['id']}?aid={STAY22_AID}"
            html += f"""
            <div style='margin-top:15px; padding:15px; background:#fff; border-radius:10px; border:1px solid #eee;'>
                <div style='display:flex; justify-content:space-between; align-items:center;'>
                    <b style='font-size:14px;'>{h['n']}</b>
                    <a href='{link}' target='_blank' style='background:#007BFF; color:#fff; text-decoration:none; padding:6px 12px; border-radius:6px; font-size:12px; font-weight:bold;'>Забронировать</a>
                </div>
                <p style='font-size:12px; color:#666; margin:8px 0 0;'>{h['d']}</p>
            </div>"""
        
        all_link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(city)}"
        html += f"<br><a href='{all_link}' target='_blank' style='display:block; text-align:center; padding:12px; background:#003580; color:#fff; text-decoration:none; border-radius:8px; font-weight:bold;'>Все отели в {city} →</a></div>"

        if redis:
            try: redis.set(db_key, html)
            except: pass

        return JSONResponse(content={"reply": html})
    except:
        return JSONResponse(content={"reply": "Ошибка. Попробуйте еще раз."})
