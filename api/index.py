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

# --- СУПЕР-БЫСТРЫЙ КОННЕКТ К REDIS ---
try:
    # Используем сразу переменные из Upstash (которые ты добавил в Env)
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

def get_data(city_query, intent):
    """Оптимизированный поиск отелей"""
    try:
        headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": "booking-com18.p.rapidapi.com"}
        # 1. Поиск ID города
        l_res = requests.get("https://booking-com18.p.rapidapi.com/stays/auto-complete", 
                             headers=headers, params={"query": city_query}, timeout=7)
        locs = l_res.json().get('data', [])
        if not locs: return None
        dest_id = locs[0]['id']
        
        # 2. Поиск отелей
        p = {"locationId": dest_id, "checkinDate": (datetime.now()+timedelta(days=30)).strftime('%Y-%m-%d'),
             "checkoutDate": (datetime.now()+timedelta(days=33)).strftime('%Y-%m-%d'), "adults": "2", "currency_code": "USD"}
        if intent == "cheap": p["sortBy"] = "price_lowest"
        
        h_res = requests.get("https://booking-com18.p.rapidapi.com/stays/search", headers=headers, params=p, timeout=10)
        data = h_res.json().get('data', [])
        if not isinstance(data, list): data = h_res.json().get('data', {}).get('hotels', [])
        return [{"id": str(x.get('hotel_id') or x.get('id')), "name": x.get('name') or x.get('hotel_name')} for x in data if x.get('id') or x.get('hotel_id')][:5]
    except: return None

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    msg = payload.message.strip().lower()
    
    # Пытаемся понять город простым способом, чтобы сэкономить время
    intent = "cheap" if "деш" in msg else "general"
    city_candidate = msg.replace("отели", "").replace("дешевые", "").replace("дешовые", "").replace("в ", "").strip()

    # Сначала проверяем базу (Redis) - это самый быстрый путь
    db_key = f"h:{city_candidate}:{intent}:ru"
    if redis:
        try:
            cached = redis.get(db_key)
            if cached: return JSONResponse(content={"reply": cached})
        except: pass

    try:
        # Если в базе нет, идем в API (это долго)
        hotels = get_data(city_candidate, intent)
        if not hotels:
            return JSONResponse(content={"reply": "Отели не найдены. Уточните название города."})

        # Генерируем финальный ответ через ИИ (один раз!)
        g_key = random.choice(groq_keys)
        g_prompt = f"Напиши на русском краткий гид по отелям в {city_candidate.capitalize()}. Отели: {json.dumps(hotels)}. Ответ ТОЛЬКО JSON: {{\"i\": \"текст\", \"cats\": [ {{\"n\": \"категория\", \"h\": {{\"id\": \"id\", \"n\": \"имя\", \"d\": \"описание\"}} }} ]}}"
        
        g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", 
                              headers={"Authorization": f"Bearer {g_key}"},
                              json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": g_prompt}], "response_format": {"type": "json_object"}}, timeout=12)
        
        res = json.loads(g_res.json()['choices'][0]['message']['content'])
        
        # Сборка HTML
        html = f"<div style='font-family:sans-serif;'><p>{res.get('i', '')}</p>"
        for cat in res['cats']:
            h = cat['h']
            link = f"https://www.stay22.com/allez/booking/{h['id']}?aid={STAY22_AID}"
            html += f"<div style='margin-top:12px; padding:12px; background:#fff; border-radius:8px; border:1px solid #eee;'><b>{h['n']}</b><br><p style='font-size:12px; color:#666;'>{h['d']}</p><a href='{link}' target='_blank' style='color:#007BFF; font-size:12px; font-weight:bold; text-decoration:none;'>Забронировать →</a></div>"
        
        html += f"<br><a href='https://www.stay22.com/allez/{STAY22_AID}?address={city_candidate}' target='_blank' style='display:block; text-align:center; padding:10px; background:#003580; color:#fff; text-decoration:none; border-radius:5px;'>Смотреть всё</a></div>"

        # Сохраняем в базу, чтобы следующий раз был мгновенным
        if redis:
            try: redis.set(db_key, html)
            except: pass

        return JSONResponse(content={"reply": html})
    except:
        return JSONResponse(content={"reply": "Ошибка связи. Попробуйте еще раз."})
