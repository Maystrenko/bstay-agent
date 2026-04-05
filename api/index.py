import os
import json
import urllib.parse
import random
import requests
import time
import re
from datetime import datetime, timedelta
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

groq_keys = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")
RAPID_HOST = "booking-com18.p.rapidapi.com"
STAY22_AID = "bstay24"

HOTEL_CACHE = {}
CACHE_TTL = 21600 

class ChatPayload(BaseModel):
    message: str
    lang: str = "en"
    chat_history: list = []

def get_hotels_data(city_name, intent="general"):
    """Поиск отелей. Если intent='cheap', включаем сортировку по цене."""
    try:
        headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": RAPID_HOST}
        l_res = requests.get(f"https://{RAPID_HOST}/stays/auto-complete", headers=headers, params={"query": city_name}, timeout=10)
        l_data = l_res.json().get('data', [])
        if not l_data: return None
        dest_id = l_data[0]['id']
        
        params = {
            "locationId": dest_id, 
            "checkinDate": (datetime.now()+timedelta(days=30)).strftime('%Y-%m-%d'),
            "checkoutDate": (datetime.now()+timedelta(days=33)).strftime('%Y-%m-%d'),
            "adults": "2", "currency_code": "USD",
            "units": "metric"
        }
        
        # Если юзер хочет дешевые — добавляем сортировку
        if intent == "cheap":
            params["sortBy"] = "price_lowest"
        
        h_res = requests.get(f"https://{RAPID_HOST}/stays/search", headers=headers, params=params, timeout=15)
        h_json = h_res.json()
        raw = h_json.get('data', [])
        if not isinstance(raw, list): 
            raw = h_json.get('data', {}).get('hotels', []) or h_json.get('data', {}).get('results', [])
        
        return [{"id": str(h.get('hotel_id') or h.get('id')), "name": h.get('name') or h.get('hotel_name')} for h in raw if (h.get('id') or h.get('hotel_id'))][:6]
    except Exception as e:
        return None

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    current_time = time.time()
    user_lang = payload.lang if payload.lang in ["ru", "en"] else "en"
    msg = payload.message.strip()
    
    try:
        g_key = random.choice(groq_keys)
        headers = {"Authorization": f"Bearer {g_key}"}

        # --- ШАГ 1: ОПРЕДЕЛЯЕМ ГОРОД И ИНТЕНТ (General или Cheap) ---
        c_sys = (
            "Determine city and intent. Intent is 'cheap' if user mentions cheap/budget/low price. "
            "Otherwise intent is 'general'. JSON ONLY: {'c': 'London', 't': 'cheap'}. "
            "Ignore typos. If no city, return 'none'."
        )
        
        c_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
            json={
                "model": "llama-3.3-70b-versatile", 
                "messages": [{"role": "system", "content": c_sys}, {"role": "user", "content": msg}], 
                "response_format": {"type": "json_object"}
            }, timeout=10)
        
        extraction = json.loads(c_res.json()['choices'][0]['message']['content'])
        city = extraction.get("c", "none")
        intent = extraction.get("t", "general")

        if city.lower() == "none":
            return JSONResponse(content={"reply": "Пожалуйста, укажите город." if user_lang == "ru" else "Specify city."})

        # --- ШАГ 2: КЭШ ---
        cache_key = f"{city.lower()}_{intent}_{user_lang}"
        if cache_key in HOTEL_CACHE and (current_time - HOTEL_CACHE[cache_key]['timestamp'] < CACHE_TTL):
            return JSONResponse(content={"reply": HOTEL_CACHE[cache_key]['html']})

        # --- ШАГ 3: API ПОИСК ---
        hotels = get_hotels_data(city, intent)
        if not hotels:
            return JSONResponse(content={"reply": f"Отели в {city} не найдены."})

        # --- ШАГ 4: ГЕНЕРАЦИЯ ОТВЕТА ПОД ЦЕЛЬ ---
        lang_name = "Russian" if user_lang == "ru" else "English"
        btn = "Book" if user_lang == "en" else "Забронировать"
        
        # Разные промпты для разных целей
        if intent == "cheap":
            g_prompt = f"List 3 cheapest hotels in {city} from this list: {json.dumps(hotels)}. In {lang_name}. JSON: {{'i': 'intro', 'cats': [ {{'n': 'Budget Choice #1', 'h': {{'id': 'id', 'n': 'name', 'd': 'desc'}} }} ], 't': 'tip'}}"
        else:
            g_prompt = f"Create a Top-3 guide (Premium, Boutique, Value) for {city} from: {json.dumps(hotels)}. In {lang_name}. JSON: {{'i': 'intro', 'cats': [ {{'n': 'Category', 'h': {{'id': 'id', 'n': 'name', 'd': 'desc'}} }} ], 't': 'tip'}}"

        g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": g_prompt}], "response_format": {"type": "json_object"}}, timeout=15)
        g = json.loads(g_res.json()['choices'][0]['message']['content'])

        # --- ШАГ 5: ВЕРСТКА ---
        html = f"<div style='font-family: Karla, sans-serif;'><p>{g['i']}</p>"
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
        if intent == "cheap":
            all_link += "&sortby=price_lowest" # Ссылка на карту тоже с дешевыми

        html += f"""
            <div style='background:#eef5ff; border-left:4px solid #007BFF; padding:15px; border-radius:8px; margin-top:25px; font-size:13px;'><b>💡 Совет:</b> {g['t']}</div>
            <br><a href='{all_link}' target='_blank' style='display:block; text-align:center; padding:15px; background:#003580; color:#fff; text-decoration:none; border-radius:10px; font-weight:bold;'>Смотреть все варианты в {city.capitalize()} →</a>
        </div>"""

        HOTEL_CACHE[cache_key] = {"timestamp": current_time, "html": html}
        return JSONResponse(content={"reply": html})

    except Exception:
        return JSONResponse(content={"reply": "Техническая ошибка. Попробуйте еще раз."})
