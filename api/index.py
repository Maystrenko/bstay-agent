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

# Вечная память (Vercel KV)
redis = Redis.from_env()

groq_keys = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")
RAPID_HOST = "booking-com18.p.rapidapi.com"
STAY22_AID = "bstay24"

class ChatPayload(BaseModel):
    message: str
    lang: str = "en"
    chat_history: list = []

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    user_lang = payload.lang if payload.lang in ["ru", "en"] else "en"
    msg = payload.message.strip()
    
    try:
        g_key = random.choice(groq_keys)
        headers = {"Authorization": f"Bearer {g_key}"}

        # 1. ОПРЕДЕЛЯЕМ ГОРОД И ЦЕЛЬ (Intent)
        c_sys = "Extract city and intent (cheap/general). JSON ONLY: {'c': 'London', 't': 'cheap'}"
        c_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "system", "content": c_sys}, {"role": "user", "content": msg}], "response_format": {"type": "json_object"}}, timeout=10)
        ext = json.loads(c_res.json()['choices'][0]['message']['content'])
        city, intent = ext.get("c", "none"), ext.get("t", "general")

        if city == "none":
            return JSONResponse(content={"reply": "Укажите город."})

        # --- 2. ПРОВЕРЯЕМ ВЕЧНУЮ ПАМЯТЬ ---
        db_key = f"h:{city.lower()}:{intent}:{user_lang}" # Сократили ключ для экономии места
        cached_html = redis.get(db_key)
        if cached_html:
            return JSONResponse(content={"reply": cached_html})

        # --- 3. ЕСЛИ НЕТ В ПАМЯТИ - ИДЕМ В RAPID ---
        h_headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": RAPID_HOST}
        l_res = requests.get(f"https://{RAPID_HOST}/stays/auto-complete", headers=h_headers, params={"query": city}, timeout=10)
        dest_id = l_res.json()['data'][0]['id']
        
        params = {"locationId": dest_id, "checkinDate": (datetime.now()+timedelta(days=30)).strftime('%Y-%m-%d'), "checkoutDate": (datetime.now()+timedelta(days=33)).strftime('%Y-%m-%d'), "adults": "2", "currency_code": "USD"}
        if intent == "cheap": params["sortBy"] = "price_lowest"
        
        h_res = requests.get(f"https://{RAPID_HOST}/stays/search", headers=h_headers, params=params, timeout=15)
        raw = h_res.json().get('data', [])
        if not isinstance(raw, list): raw = h_res.json().get('data', {}).get('hotels', [])
        hotels = [{"id": str(h.get('hotel_id') or h.get('id')), "name": h.get('name') or h.get('hotel_name')} for h in raw if h.get('id') or h.get('hotel_id')][:6]

        # --- 4. ГЕНЕРИРУЕМ ОТВЕТ ---
        lang_full = "Russian" if user_lang == "ru" else "English"
        g_prompt = f"Create Top-3 hotel guide for {city} in {lang_full}. Use: {json.dumps(hotels)}. JSON: {{'i': 'intro text', 'cats': [ {{'n': 'Category', 'h': {{'id': 'id', 'n': 'name', 'd': 'desc'}} }} ]}}"
        g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": g_prompt}], "response_format": {"type": "json_object"}}, timeout=15)
        g = json.loads(g_res.json()['choices'][0]['message']['content'])

        # ФИКС .intro: если ИИ тупит, ставим нормальный текст
        intro = g.get('i', '')
        if len(intro) < 10 or intro.startswith('.'):
            intro = f"Вот отличные варианты в городе {city.capitalize()}:" if user_lang == "ru" else f"Here are great options in {city.capitalize()}:"

        btn = "Забронировать" if user_lang == "ru" else "Book"
        html = f"<div style='font-family:Karla,sans-serif;'><p>{intro}</p>"
        for cat in g['cats']:
            h = cat['h']
            link = f"https://www.stay22.com/allez/booking/{h['id']}?aid={STAY22_AID}"
            html += f"<div style='margin-top:20px;'><span style='background:#003580; color:#fff; padding:4px 12px; border-radius:20px; font-size:11px; font-weight:bold;'>{cat['n']}</span><div style='margin-top:10px; padding:15px; background:#fff; border-radius:12px; border:1px solid #eee; box-shadow:0 4px 12px rgba(0,0,0,0.03);'><div style='display:flex; justify-content:space-between; align-items:center;'><span style='font-weight:bold; font-size:15px;'>{h['n']}</span><a href='{link}' target='_blank' style='background:#007BFF; color:#fff; text-decoration:none; padding:8px 18px; border-radius:8px; font-weight:bold; font-size:13px;'>{btn}</a></div><p style='font-size:13px; color:#666; margin:10px 0 0;'>{h['d']}</p></div></div>"
        
        all_link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(city)}"
        html += f"<br><a href='{all_link}' target='_blank' style='display:block; text-align:center; padding:15px; background:#003580; color:#fff; text-decoration:none; border-radius:10px; font-weight:bold;'>Смотреть все →</a></div>"

        # СОХРАНЯЕМ В ВЕЧНУЮ ПАМЯТЬ
        redis.set(db_key, html) 
        return JSONResponse(content={"reply": html})
    except:
        return JSONResponse(content={"reply": "Ошибка. Попробуйте еще раз."})
