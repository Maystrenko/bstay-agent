import os
import json
import urllib.parse
import time
import random
import requests
from datetime import datetime, timedelta
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# Настройки
groq_keys = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")
RAPID_HOST = "booking-com18.p.rapidapi.com"
STAY22_AID = "bstay24"
LANG_MAP = {'ru': 'Russian', 'en': 'English', 'de': 'German', 'fr': 'French', 'es': 'Spanish'}

class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list
    lang: str = "en"

def get_hotels_data(city_name, lang='ru'):
    """Получение 10 реальных отелей с их ID"""
    if not RAPID_API_KEY: return None, "No API Key"
    headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": RAPID_HOST}
    try:
        # 1. Локация
        loc_res = requests.get(f"https://{RAPID_HOST}/stays/auto-complete", headers=headers, params={"query": city_name}, timeout=7)
        loc_data = loc_res.json()
        loc_list = loc_data if isinstance(loc_data, list) else loc_data.get('data', [])
        if not loc_list: return None, "City not found"
        dest_id = loc_list[0].get('id')

        # 2. Поиск отелей
        in_d = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
        out_d = (datetime.now() + timedelta(days=33)).strftime('%Y-%m-%d')
        params = {"locationId": dest_id, "checkinDate": in_d, "checkoutDate": out_d, "adults": "2", "rooms": "1", "currency_code": "USD"}
        
        res = requests.get(f"https://{RAPID_HOST}/stays/search", headers=headers, params=params, timeout=12)
        search_data = res.json()
        
        hotels_raw = []
        if isinstance(search_data, list): hotels_raw = search_data
        else:
            d_block = search_data.get('data', {})
            hotels_raw = d_block if isinstance(d_block, list) else (d_block.get('hotels', []) or d_block.get('results', []))
        
        refined = [{"id": str(h.get('hotel_id') or h.get('id')), "name": h.get('name') or h.get('hotel_name', 'Hotel')} for h in hotels_raw if h.get('id') or h.get('hotel_id')]
        return refined[:10], None
    except Exception as e:
        return None, str(e)

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    try:
        t_lang = LANG_MAP.get(payload.lang, "Russian")
        g_key = random.choice(groq_keys) if groq_keys else None
        if not g_key: return JSONResponse(content={"reply": "AI Key missing."})

        # 1. Извлекаем город
        city_prompt = f"Extract only city name in English from: '{payload.message}'. JSON ONLY: {{\"city\": \"Name\"}}"
        c_res = requests.post("https://api.groq.com/openai/v1/chat/completions", 
            headers={"Authorization": f"Bearer {g_key}"}, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": city_prompt}], "response_format": {"type": "json_object"}}, timeout=10)
        city_name = c_res.json()['choices'][0]['message']['content']
        city_name = json.loads(city_name).get("city", "none")

        if city_name.lower() == "none":
            return JSONResponse(content={"reply": "Уточните город, пожалуйста."})

        # 2. Получаем отели
        hotels, err = get_hotels_data(city_name, payload.lang)
        if not hotels:
            return JSONResponse(content={"reply": f"Не нашел отелей в {city_name}. Попробуйте другой город."})

        # 3. Генерируем гид
        guide_prompt = f"""
        Based on these real hotels in {city_name}: {json.dumps(hotels)}.
        Create a detailed travel guide in {t_lang}. Categorize: Luxury, Boutique, Budget.
        Write 1-2 sentences for each. Return ONLY JSON:
        {{ "intro": "...", "categories": [ {{ "name": "...", "hotels": [ {{ "name": "...", "id": "...", "desc": "..." }} ] }} ], "tips": "..." }}
        """
        g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", 
            headers={"Authorization": f"Bearer {g_key}"}, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": guide_prompt}], "response_format": {"type": "json_object"}}, timeout=20)
        
        g_data = json.loads(g_res.json()['choices'][0]['message']['content'])

        # 4. Сборка HTML
        html = f"<div style='line-height:1.5;'><p>{g_data.get('intro','')}</p>"
        for cat in g_data.get('categories', []):
            html += f"<h4 style='color:#003580; margin:15px 0 5px; border-bottom:1px solid #eee;'>{cat['name']}</h4>"
            for h in cat.get('hotels', []):
                link = f"https://www.stay22.com/allez/booking/{h['id']}?aid={STAY22_AID}"
                html += f"""
                <div style='margin-bottom:12px; padding:10px; background:#f9f9f9; border-radius:8px; border:1px solid #eee;'>
                    <div style='display:flex; justify-content:space-between; align-items:center;'>
                        <b style='font-size:14px;'>{h['name']}</b>
                        <a href='{link}' target='_blank' style='background:#007BFF; color:#fff; text-decoration:none; padding:5px 12px; border-radius:6px; font-size:12px; font-weight:bold;'>Book</a>
                    </div>
                    <p style='font-size:12px; color:#666; margin:5px 0 0;'>{h['desc']}</p>
                </div>"""
        html += f"<p style='background:#e9f7ef; padding:10px; border-radius:8px; font-size:12px;'><b>Совет:</b> {g_data.get('tips','')}</p></div>"

        return JSONResponse(content={"reply": html})
    except Exception as e:
        return JSONResponse(content={"reply": f"Ошибка: {str(e)[:50]}"})
