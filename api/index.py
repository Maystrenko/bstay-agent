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

# --- БЛОК ПОДКЛЮЧЕНИЯ К БАЗЕ (Исправлено для Vercel) ---
try:
    # Пробуем достать данные из разных возможных имен переменных Vercel
    u = os.environ.get("UPSTASH_REDIS_REST_URL") or os.environ.get("REDIS_URL")
    t = os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    
    if u and t:
        # Если есть и URL и Token отдельно
        redis = Redis(url=u, token=t)
        print("✅ Redis: Connected via URL and Token")
    elif u:
        # Если есть только URL, принудительно ставим его в окружение для библиотеки
        os.environ["UPSTASH_REDIS_REST_URL"] = u
        redis = Redis.from_env()
        print("✅ Redis: Connected via Environment URL")
    else:
        redis = None
        print("⚠️ Redis: Environment variables not found")
except Exception as e:
    redis = None
    print(f"❌ Redis Init Error: {e}")

# Конфигурация ключей и API
groq_keys = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")
RAPID_HOST = "booking-com18.p.rapidapi.com"
STAY22_AID = "bstay24"

class ChatPayload(BaseModel):
    message: str
    lang: str = "en"
    chat_history: list = []

def get_hotels_from_api(city, intent="general"):
    """Запрос к Booking API через RapidAPI"""
    try:
        headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": RAPID_HOST}
        # 1. Поиск ID локации
        l_res = requests.get(f"https://{RAPID_HOST}/stays/auto-complete", headers=headers, params={"query": city}, timeout=10)
        l_data = l_res.json().get('data', [])
        if not l_data: return None
        dest_id = l_data[0]['id']
        
        # 2. Поиск отелей (через 30 дней на 3 ночи)
        params = {
            "locationId": dest_id, 
            "checkinDate": (datetime.now()+timedelta(days=30)).strftime('%Y-%m-%d'),
            "checkoutDate": (datetime.now()+timedelta(days=33)).strftime('%Y-%m-%d'),
            "adults": "2", "currency_code": "USD"
        }
        if intent == "cheap":
            params["sortBy"] = "price_lowest"
            
        h_res = requests.get(f"https://{RAPID_HOST}/stays/search", headers=headers, params=params, timeout=15)
        h_json = h_res.json()
        raw = h_json.get('data', [])
        if not isinstance(raw, list):
            raw = h_json.get('data', {}).get('hotels', []) or h_json.get('data', {}).get('results', [])
            
        return [{"id": str(h.get('hotel_id') or h.get('id')), "name": h.get('name') or h.get('hotel_name')} for h in raw if h.get('id') or h.get('hotel_id')][:6]
    except Exception as e:
        print(f"RapidAPI Error: {e}")
        return None

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    user_lang = payload.lang if payload.lang in ["ru", "en"] else "en"
    msg = payload.message.strip()
    
    try:
        g_key = random.choice(groq_keys)
        headers = {"Authorization": f"Bearer {g_key}"}

        # --- 1. ОПРЕДЕЛЯЕМ ГОРОД И ИНТЕНТ ---
        c_sys = "Extract city and intent (cheap/general). JSON ONLY: {'c': 'London', 't': 'cheap'}. Normalize city to English."
        c_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "system", "content": c_sys}, {"role": "user", "content": msg}], "response_format": {"type": "json_object"}}, timeout=10)
        ext = json.loads(c_res.json()['choices'][0]['message']['content'])
        city, intent = ext.get("c", "none"), ext.get("t", "general")

        if city == "none" or len(city) < 2:
            return JSONResponse(content={"reply": "Пожалуйста, напишите название города." if user_lang == "ru" else "Please specify a city."})

        # --- 2. ПРОВЕРЯЕМ БАЗУ ДАННЫХ (REDIS) ---
        db_key = f"h:{city.lower()}:{intent}:{user_lang}"
        if redis:
            try:
                cached_html = redis.get(db_key)
                if cached_html:
                    # Если нашли в базе - отдаем мгновенно!
                    return JSONResponse(content={"reply": cached_html})
            except Exception as e:
                print(f"Redis Read Error: {e}")

        # --- 3. ЕСЛИ НЕТ В БАЗЕ - ИДЕМ В API ---
        hotels = get_hotels_from_api(city, intent)
        if not hotels:
            return JSONResponse(content={"reply": "Отели не найдены. Попробуйте другой город."})

        # --- 4. ГЕНЕРИРУЕМ КРАСИВЫЙ ОТВЕТ ЧЕРЕЗ ИИ ---
        lang_full = "Russian" if user_lang == "ru" else "English"
        btn = "Забронировать" if user_lang == "ru" else "Book"
        
        if intent == "cheap":
            g_prompt = f"List 3 cheapest hotels in {city} in {lang_full}. Use: {json.dumps(hotels)}. JSON: {{'i': 'intro', 'cats': [ {{'n': 'Budget #1', 'h': {{'id': 'id', 'n': 'name', 'd': 'desc'}} }} ]}}"
        else:
            g_prompt = f"Create Top-3 hotel guide for {city} in {lang_full}. Use: {json.dumps(hotels)}. JSON: {{'i': 'intro', 'cats': [ {{'n': 'Category', 'h': {{'id': 'id', 'n': 'name', 'd': 'desc'}} }} ]}}"

        g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": g_prompt}], "response_format": {"type": "json_object"}}, timeout=15)
        g = json.loads(g_res.json()['choices'][0]['message']['content'])

        # Исправляем .intro если ИИ выдал техническое слово
        intro = g.get('i', '')
        if len(intro) < 10 or intro.lower().startswith('.intro'):
            intro = f"Вот отличные варианты в городе {city.capitalize()}:" if user_lang == "ru" else f"Here are great options in {city.capitalize()}:"

        # Сборка HTML карточек
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
        if intent == "cheap": all_link += "&sortby=price_lowest"
        
        html += f"<br><a href='{all_link}' target='_blank' style='display:block; text-align:center; padding:15px; background:#003580; color:#fff; text-decoration:none; border-radius:10px; font-weight:bold;'>Смотреть все варианты →</a></div>"

        # --- 5. СОХРАНЯЕМ В БАЗУ НА БУДУЩЕЕ ---
        if redis:
            try:
                redis.set(db_key, html)
                print(f"✅ Saved to Cache: {db_key}")
            except Exception as e:
                print(f"Redis Write Error: {e}")

        return JSONResponse(content={"reply": html})

    except Exception as e:
        print(f"Global Error: {e}")
        return JSONResponse(content={"reply": "Ошибка соединения. Пожалуйста, попробуйте еще раз."})
