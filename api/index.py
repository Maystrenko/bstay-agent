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

# Ключи и настройки
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
    if not RAPID_API_KEY: return None, "No API Key"
    headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": RAPID_HOST}
    try:
        # 1. Поиск локации
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
        
        # Собираем только те, у которых есть ID
        refined = []
        for h in hotels_raw:
            hid = h.get('hotel_id') or h.get('id')
            if hid:
                refined.append({"id": str(hid), "name": h.get('name') or h.get('hotel_name', 'Hotel')})
        
        return refined[:10], None
    except Exception as e:
        return None, str(e)

def safe_groq_request(prompt):
    """Надежный запрос к Groq с обработкой ошибок"""
    if not groq_keys: return None
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions", 
            headers={"Authorization": f"Bearer {random.choice(groq_keys)}"}, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "response_format": {"type": "json_object"}}, 
            timeout=15)
        res_json = r.json()
        if 'choices' in res_json:
            return json.loads(res_json['choices'][0]['message']['content'])
    except:
        return None
    return None

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    try:
        t_lang = LANG_MAP.get(payload.lang, "Russian")
        
        # 1. Извлекаем город
        city_data = safe_groq_request(f"Identify city in English from: '{payload.message}'. JSON: {{\"city\": \"Name\"}}")
        city_name = city_data.get("city", "none") if city_data else "none"

        if city_name.lower() == "none" or len(city_name) < 3:
            return JSONResponse(content={"reply": "Пожалуйста, уточните название города, чтобы я мог составить гид."})

        # 2. Получаем отели
        hotels, err = get_hotels_data(city_name, payload.lang)
        if not hotels:
            return JSONResponse(content={"reply": f"Отели в {city_name} сейчас недоступны. Попробуйте другой город."})

        # 3. Генерируем расширенный гид
        guide_data = safe_groq_request(f"""
        Real hotels in {city_name}: {json.dumps(hotels)}.
        Create a professional travel guide in {t_lang}. Categorize: Luxury, Boutique, Budget.
        Return JSON: {{"intro": "...", "categories": [{{"name": "...", "hotels": [{{"name": "...", "id": "...", "desc": "..."}}]}}], "tips": "..."}}
        """)

        if not guide_data:
            return JSONResponse(content={"reply": "ИИ немного устал, но я нашел список отелей. Попробуйте обновить страницу."})

        # 4. Сборка HTML
        html = f"<div style='font-family: sans-serif; line-height: 1.6;'>"
        html += f"<p>{guide_data.get('intro', '')}</p>"

        for cat in guide_data.get('categories', []):
            html += f"<h3 style='color: #003580; margin: 20px 0 10px; border-bottom: 2px solid #eee; font-size: 1.1em;'>{cat['name']}</h3>"
            for h in cat.get('hotels', []):
                link = f"https://www.stay22.com/allez/booking/{h['id']}?aid={STAY22_AID}"
                html += f"""
                <div style='margin-bottom: 15px; padding: 12px; background: #fcfcfc; border-radius: 10px; border: 1px solid #eee;'>
                    <div style='display: flex; justify-content: space-between; align-items: flex-start; gap: 10px;'>
                        <strong style='font-size: 14px;'>{h['name']}</strong>
                        <a href='{link}' target='_blank' style='background: #007BFF; color: white; text-decoration: none; padding: 6px 14px; border-radius: 6px; font-size: 12px; font-weight: bold; white-space: nowrap;'>Book Now</a>
                    </div>
                    <p style='font-size: 13px; color: #555; margin: 8px 0 0 0;'>{h['desc']}</p>
                </div>"""

        html += f"<div style='background: #e9f7ef; padding: 15px; border-radius: 10px; margin-top: 15px;'><strong>💡 Советы:</strong><br><small>{guide_data.get('tips', '')}</small></div>"
        
        city_enc = urllib.parse.quote(city_name)
        html += f"<br><a href='https://www.stay22.com/allez/{STAY22_AID}?address={city_enc}' target='_blank' style='display: block; text-align: center; padding: 15px; background: #003580; color: white; text-decoration: none; border-radius: 10px; font-weight: bold;'>Смотреть все варианты в {city_name}</a></div>"

        return JSONResponse(content={"reply": html})

    except Exception as e:
        # Даже если всё упало, возвращаем человеческую ошибку вместо undefined
        return JSONResponse(content={"reply": f"Извините, произошла техническая ошибка: {str(e)[:50]}"})
