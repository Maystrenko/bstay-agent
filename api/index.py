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
from upstash_redis import Redis

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# --- ПРЯМОЕ ПОДКЛЮЧЕНИЕ К REDIS ---
try:
    # Используй именно эти имена в Vercel Settings
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
    """Строгий поиск отелей по конкретному городу"""
    try:
        headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": "booking-com18.p.rapidapi.com"}
        
        # 1. СТРОГИЙ ПОИСК ID ГОРОДА (Auto-complete)
        l_res = requests.get("https://booking-com18.p.rapidapi.com/stays/auto-complete", 
                             headers=headers, params={"query": city_query}, timeout=7)
        locs = l_res.json().get('data', [])
        if not locs: return None
        
        # Берем первый ID. Если город указан верно, Болгарии не будет.
        dest_id = locs[0]['id']
        
        # 2. ПОИСК ОТЕЛЕЙ
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
    raw_msg = payload.message.strip().lower()
    
    # 1. ЧИСТИМ ГОРОД (Убираем мусор, чтобы API не выдало Болгарию)
    intent = "cheap" if any(x in raw_msg for x in ["деш", "бюдж", "cheap"]) else "general"
    # Удаляем слова-паразиты
    city = raw_msg.replace("отели", "").replace("дешевые", "").replace("дешовые", "").replace("в ", "").replace("хочу", "").replace("найди", "").strip()

    if not city:
        return JSONResponse(content={"reply": "Напишите название города."})

    # 2. ПРОВЕРКА КЭША (Если есть - отдаем за 0.1 сек)
    db_key = f"h:{city}:{intent}:ru"
    if redis:
        try:
            cached = redis.get(db_key)
            if cached: return JSONResponse(content={"reply": cached})
        except: pass

    try:
        # 3. ЕСЛИ НЕТ В БАЗЕ - ИДЕМ В API
        hotels = get_hotels(city, intent)
        if not hotels:
            return JSONResponse(content={"reply": f"Отели в городе '{city.capitalize()}' не найдены."})

        # 4. ГЕНЕРАЦИЯ ГИДА (Один запрос к Groq)
        g_key = random.choice(groq_keys)
        prompt = f"Напиши на русском гид по 3 отелям в {city.capitalize()}. Данные: {json.dumps(hotels)}. JSON ONLY: {{\"i\": \"вступление\", \"cats\": [ {{\"n\": \"категория\", \"h\": {{\"id\": \"id\", \"n\": \"имя\", \"d\": \"описание\"}} }} ]}}"
        
        g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", 
                              headers={"Authorization": f"Bearer {g_key}"},
                              json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "response_format": {"type": "json_object"}}, timeout=12)
        
        res_data = json.loads(g_res.json()['choices'][0]['message']['content'])
        
        # 5. СБОРКА HTML
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
                <p style='font-size:12px; color:#666; margin:6px 0 0;'>{h['d'] or 'Отличный вариант для проживания.'}</p>
            </div>"""
        
        all_link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(city)}"
        html += f"<br><a href='{all_link}' target='_blank' style='display:block; text-align:center; padding:12px; background:#003580; color:#fff; text-decoration:none; border-radius:6px; font-weight:bold;'>Смотреть все отели в {city.capitalize()} →</a></div>"

        # 6. СОХРАНЕНИЕ В БАЗУ
        if redis:
            try:
                redis.set(db_key, html)
            except: pass

        return JSONResponse(content={"reply": html})
    except:
        return JSONResponse(content={"reply": "Сервис временно перегружен. Попробуйте через минуту."})
