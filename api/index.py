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
gemini_keys = [k.strip() for k in os.environ.get("GEMINI_API_KEY", "").split(",") if k.strip()]
groq_keys = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")

# ВАЖНО: Хост из твоего скриншота!
RAPID_HOST = "booking-com18.p.rapidapi.com" 
STAY22_AID = "bstay24"
LANG_MAP = {'ru': 'Russian', 'en': 'English', 'de': 'German', 'fr': 'French', 'es': 'Spanish'}

class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list
    lang: str = "en"

def get_hotels_safe(city_name, lang='ru'):
    if not RAPID_API_KEY: return None, "No API Key"
    headers = {
        "X-RapidAPI-Key": RAPID_API_KEY,
        "X-RapidAPI-Host": RAPID_HOST
    }
    try:
        # 1. Поиск ID локации
        loc_url = f"https://{RAPID_HOST}/v1/hotels/locations"
        loc_res = requests.get(loc_url, headers=headers, params={"name": city_name, "locale": "en-gb"}, timeout=5)
        
        if loc_res.status_code != 200:
            return None, f"API Error {loc_res.status_code}"
            
        loc_data = loc_res.json()
        if not loc_data or not isinstance(loc_data, list):
            return None, "City not found"
        
        # Берем первый результат (обычно это город)
        d_id = loc_data[0].get('dest_id')
        d_type = loc_data[0].get('dest_type')

        # 2. Даты (на 30 дней вперед)
        in_d = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
        out_d = (datetime.now() + timedelta(days=33)).strftime('%Y-%m-%d')

        # 3. Поиск отелей
        search_url = f"https://{RAPID_HOST}/v1/hotels/search"
        params = {
            "dest_id": d_id, "dest_type": d_type,
            "checkin_date": in_d, "checkout_date": out_d,
            "adults_number": "2", "room_number": "1",
            "order_by": "popularity", "units": "metric", "locale": lang, "currency": "USD"
        }
        
        search_res = requests.get(search_url, headers=headers, params=params, timeout=8)
        results = search_res.json().get('result', [])
        return results[:3], None
        
    except Exception as e:
        return None, f"Err: {str(e)[:20]}"

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    try:
        t_lang = LANG_MAP.get(payload.lang, "Russian")
        prompt = f"Extract city (English) and write 2-sentence cool greeting in {t_lang}. User: {payload.message}. Return JSON: {{\"city\": \"City\", \"text\": \"Greeting\"}}"
        
        ai_res = None
        engine = "None"

        # Пробуем Groq (он у тебя на скринах работает отлично)
        if groq_keys:
            try:
                g_key = random.choice(groq_keys)
                r = requests.post("https://api.groq.com/openai/v1/chat/completions", 
                    headers={"Authorization": f"Bearer {g_key}"}, 
                    json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "response_format": {"type": "json_object"}}, 
                    timeout=8)
                ai_res = r.json()['choices'][0]['message']['content']
                engine = "Groq"
            except: pass

        if not ai_res: return JSONResponse(content={"reply": "ИИ занят, попробуй через секунду."})

        data = json.loads(ai_res[ai_res.find('{'):ai_res.rfind('}')+1])
        city = data.get("city", "none")
        greeting = data.get("text", "Нашел кое-что интересное!")

        # Поиск отелей
        hotels_html = ""
        api_info = "Live"
        if city.lower() != "none":
            hotels, err = get_hotels_safe(city, payload.lang)
            if hotels:
                hotels_html = "<div style='margin-top:15px; display:flex; flex-direction:column; gap:12px;'>"
                for h in hotels:
                    name = h.get('hotel_name', 'Hotel')
                    price = int(h.get('min_total_price', 0))
                    img = h.get('main_photo_url', '').replace('square60', 'square300')
                    link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(name)}"
                    
                    hotels_html += f"""
                    <div style='background:#fff; border:1px solid #eee; border-radius:12px; overflow:hidden; box-shadow:0 4px 12px rgba(0,0,0,0.1);'>
                        <img src='{img}' style='width:100%; height:140px; object-fit:cover;'>
                        <div style='padding:12px;'>
                            <div style='font-weight:bold; font-size:15px; color:#333;'>{name}</div>
                            <div style='font-size:13px; color:#28a745; margin:6px 0; font-weight:bold;'>от {price} USD за 3 ночи</div>
                            <a href='{link}' target='_blank' style='display:block; text-align:center; padding:10px; background:#007BFF; color:white; text-decoration:none; border-radius:8px; font-weight:bold; font-size:13px;'>Забронировать</a>
                        </div>
                    </div>"""
                hotels_html += "</div>"
            if err: api_info = err

        # Кнопка и подпись
        city_enc = urllib.parse.quote(city)
        main_url = f"https://www.stay22.com/allez/{STAY22_AID}?address={city_enc}&link=https://www.booking.com/searchresults.html?ss={city_enc}"
        btn_text = f"🏨 Все отели в {city}" if payload.lang == 'ru' else f"🏨 View hotels in {city}"
        
        footer = f"<br><a href='{main_url}' target='_blank' style='display:inline-block; padding:15px; background:#003580; color:white; text-decoration:none; border-radius:8px; font-weight:bold; width:100%; text-align:center; box-sizing:border-box;'>{btn_text}</a><br><small style='color:gray; font-size:9px;'>Engine: {engine} | {api_info}</small>"

        return JSONResponse(content={"reply": greeting + hotels_html + footer})
    except Exception as e:
        return JSONResponse(content={"reply": f"Ошибка: {str(e)}"})
