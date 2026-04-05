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
import redis as py_redis

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# --- ПОДКЛЮЧЕНИЕ К REDIS CLOUD (redis.io) ---
redis_db = None
try:
    url = os.environ.get("REDIS_URL")
    if url:
        redis_db = py_redis.from_url(url, decode_responses=True, socket_timeout=5)
        print("✅ Redis Cloud: Connected!")
except Exception as e:
    print(f"❌ Redis Error: {e}")

groq_keys = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")
STAY22_AID = "bstay24"

class ChatPayload(BaseModel):
    message: str

def get_hotels(city_en, intent):
    """Поиск строго по английскому названию города"""
    try:
        headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": "booking-com18.p.rapidapi.com"}
        
        # 1. Поиск ID города (именно на английском он работает на 100% точно)
        l_res = requests.get("https://booking-com18.p.rapidapi.com/stays/auto-complete", 
                             headers=headers, params={"query": city_en}, timeout=10)
        locs = l_res.json().get('data', [])
        if not locs: return None
        
        # Берем первый ID из списка
        dest_id = locs[0]['id']
        
        # 2. Поиск отелей
        params = {
            "locationId": dest_id, 
            "checkinDate": (datetime.now()+timedelta(days=30)).strftime('%Y-%m-%d'),
            "checkoutDate": (datetime.now()+timedelta(days=33)).strftime('%Y-%m-%d'),
            "adults": "2", "currency_code": "USD"
        }
        if intent == "cheap": params["sortBy"] = "price_lowest"
        
        h_res = requests.get("https://booking-com18.p.rapidapi.com/stays/search", headers=headers, params=params, timeout=15)
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
        # --- ШАГ 1: ПЕРЕВОД И ОЧИСТКА ГОРОДА (Фикс Болгарии) ---
        p_extract = f"Extract the city name in English from: '{msg}'. Respond ONLY with the city name. Example: 'London'."
        c_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": p_extract}]}, timeout=7)
        
        city_en = c_res.json()['choices'][0]['message']['content'].strip().replace(".", "").replace("'", "")
        intent = "cheap" if any(x in msg for x in ["деш", "low", "бюдж"]) else "general"

        if len(city_en) < 2:
            return JSONResponse(content={"reply": "Пожалуйста, укажите город."})

        # --- ШАГ 2: ПРОВЕРКА REDIS ---
        db_key = f"h:{city_en.lower()}:{intent}:ru"
        if redis_db:
            try:
                cached = redis_db.get(db_key)
                if cached: return JSONResponse(content={"reply": cached})
            except: pass

        # --- ШАГ 3: ПОИСК ---
        hotels = get_hotels(city_en, intent)
        if not hotels:
            return JSONResponse(content={"reply": f"Отели в {city_en} не найдены."})

        # --- ШАГ 4: ГЕНЕРАЦИЯ ОТВЕТА ---
        g_prompt = f"Напиши на русском краткий гид по 3 отелям в {city_en}. Данные: {json.dumps(hotels)}. Ответ ТОЛЬКО в формате JSON: {{\"i\": \"текст\", \"cats\": [ {{\"n\": \"категория\", \"h\": {{\"id\": \"id\", \"n\": \"имя\", \"d\": \"описание\"}} }} ]}}"
        g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": g_prompt}], "response_format": {"type": "json_object"}}, timeout=15)
        
        res_data = json.loads(g_res.json()['choices'][0]['message']['content'])
        
        # Сборка HTML (Karla font для стиля)
        html = f"<div style='font-family:sans-serif;'><p>{res_data.get('i', '')}</p>"
        for cat in res_data['cats']:
            h = cat['h']
            link = f"https://www.stay22.com/allez/booking/{h['id']}?aid={STAY22_AID}"
            html += f"""
            <div style='margin-top:15px; padding:15px; background:#fff; border-radius:10px; border:1px solid #eee; box-shadow:0 2px 5px rgba(0,0,0,0.05);'>
                <div style='display:flex; justify-content:space-between; align-items:center;'>
                    <b style='font-size:14px;'>{h['n']}</b>
                    <a href='{link}' target='_blank' style='background:#007BFF; color:#fff; text-decoration:none; padding:6px 12px; border-radius:6px; font-size:12px; font-weight:bold;'>Забронировать</a>
                </div>
                <p style='font-size:12px; color:#666; margin:8px 0 0;'>{h['d']}</p>
            </div>"""
        
        all_link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(city_en)}"
        html += f"<br><a href='{all_link}' target='_blank' style='display:block; text-align:center; padding:12px; background:#003580; color:#fff; text-decoration:none; border-radius:8px; font-weight:bold;'>Все отели →</a></div>"

        # --- ШАГ 5: СОХРАНЕНИЕ ---
        if redis_db:
            try:
                redis_db.set(db_key, html, ex=86400) # Кэш на 24 часа
            except: pass

        return JSONResponse(content={"reply": html})
    except Exception as e:
        return JSONResponse(content={"reply": "Ошибка. Попробуйте еще раз."})
