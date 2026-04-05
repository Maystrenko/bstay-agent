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

# --- ПОДКЛЮЧЕНИЕ К REDIS CLOUD ---
redis_db = None
try:
    # Используем REDIS_URL, который ты видишь в Vercel Settings
    url = os.environ.get("REDIS_URL")
    if url:
        # decode_responses=True позволяет получать текст, а не байты
        redis_db = py_redis.from_url(url, decode_responses=True)
        print("✅ Redis Cloud: Connected!")
    else:
        print("⚠️ Redis: REDIS_URL not found in environment")
except Exception as e:
    print(f"❌ Redis Connection Error: {e}")

groq_keys = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")
STAY22_AID = "bstay24"

class ChatPayload(BaseModel):
    message: str

def get_hotels(city_query, intent):
    """Поиск через Booking API"""
    try:
        headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": "booking-com18.p.rapidapi.com"}
        # 1. Находим ID города
        l_res = requests.get("https://booking-com18.p.rapidapi.com/stays/auto-complete", 
                             headers=headers, params={"query": city_query}, timeout=10)
        locs = l_res.json().get('data', [])
        if not locs: return None
        dest_id = locs[0]['id']
        
        # 2. Ищем отели
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
    except: return None

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    msg = payload.message.strip().lower()
    
    # Извлекаем название города (убираем лишнее)
    intent = "cheap" if any(x in msg for x in ["деш", "low", "бюдж"]) else "general"
    city = msg.replace("отели", "").replace("дешевые", "").replace("дешовые", "").replace("в ", "").strip()
    
    if not city:
        return JSONResponse(content={"reply": "Напишите название города."})

    # --- ПРОВЕРКА КЭША (REDIS CLOUD) ---
    db_key = f"h:{city}:{intent}:ru"
    if redis_db:
        try:
            cached = redis_db.get(db_key)
            if cached:
                print(f"🚀 Cache Hit: {db_key}")
                return JSONResponse(content={"reply": cached})
        except Exception as e:
            print(f"Redis Read Error: {e}")

    try:
        # --- ЗАПРОС К API (Если в кэше нет) ---
        hotels = get_hotels(city, intent)
        if not hotels:
            return JSONResponse(content={"reply": f"Отели в {city.capitalize()} не найдены."})

        # --- ГЕНЕРАЦИЯ ЧЕРЕЗ GROQ ---
        g_key = random.choice(groq_keys)
        prompt = f"Напиши на русском гид по 3 отелям в {city.capitalize()}. Используй: {json.dumps(hotels)}. JSON ONLY: {{\"i\": \"текст\", \"cats\": [ {{\"n\": \"категория\", \"h\": {{\"id\": \"id\", \"n\": \"имя\", \"d\": \"описание\"}} }} ]}}"
        
        g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", 
                              headers={"Authorization": f"Bearer {g_key}"},
                              json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "response_format": {"type": "json_object"}}, timeout=15)
        
        res_data = json.loads(g_res.json()['choices'][0]['message']['content'])
        
        # Сборка HTML
        intro = res_data.get('i', f"Вот отличные варианты в {city.capitalize()}:")
        html = f"<div style='font-family:Karla,sans-serif;'><p>{intro}</p>"
        for cat in res_data['cats']:
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
        html += f"<br><a href='{all_link}' target='_blank' style='display:block; text-align:center; padding:12px; background:#003580; color:#fff; text-decoration:none; border-radius:8px; font-weight:bold;'>Все отели →</a></div>"

        # --- СОХРАНЕНИЕ В КЭШ (На 24 часа) ---
        if redis_db:
            try:
                redis_db.set(db_key, html, ex=86400)
                print(f"✅ Saved to Redis: {db_key}")
            except Exception as e:
                print(f"Redis Write Error: {e}")

        return JSONResponse(content={"reply": html})
    except:
        return JSONResponse(content={"reply": "Ошибка. Попробуйте еще раз."})
