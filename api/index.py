import os
import json
import urllib.parse
import time
from datetime import datetime, timedelta
import random
import requests
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Пытаемся импортировать новый SDK Gemini
try:
    from google import genai
    SDK_AVAILABLE = True
except:
    SDK_AVAILABLE = False

app = FastAPI()

# Настройка CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Переменные окружения
gemini_keys = [k.strip() for k in os.environ.get("GEMINI_API_KEY", "").split(",") if k.strip()]
groq_keys = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")

STAY22_AID = "bstay24"
LANG_MAP = {'ru': 'Russian', 'en': 'English', 'de': 'German', 'fr': 'French', 'es': 'Spanish'}

# СТРУКТУРА ДАННЫХ (Важно!)
class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list
    lang: str = "en"

# ФУНКЦИЯ ПОИСКА ОТЕЛЕЙ
def get_hotels(city_name, lang='ru'):
    if not RAPID_API_KEY: return None, "No RapidAPI Key"
    headers = {
        "X-RapidAPI-Key": RAPID_API_KEY,
        "X-RapidAPI-Host": "booking-com.p.rapidapi.com"
    }
    try:
        # 1. ID города
        loc_res = requests.get("https://booking-com.p.rapidapi.com/v1/hotels/locations", 
                               headers=headers, params={"name": city_name, "locale": "en-gb"}, timeout=5)
        locations = loc_res.json()
        if not locations: return None, "City not found"
        dest_id = locations[0]['dest_id']
        dest_type = locations[0]['dest_type']

        # 2. Даты (через 30 дней)
        checkin = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
        checkout = (datetime.now() + timedelta(days=33)).strftime('%Y-%m-%d')

        # 3. Поиск отелей
        search_params = {
            "dest_id": dest_id, "dest_type": dest_type,
            "checkin_date": checkin, "checkout_date": checkout,
            "room_number": "1", "adults_number": "2",
            "order_by": "popularity", "units": "metric", "locale": lang, "currency": "USD"
        }
        search_res = requests.get("https://booking-com.p.rapidapi.com/v1/hotels/search", 
                                  headers=headers, params=search_params, timeout=8)
        return search_res.json().get('result', [])[:3], None
    except Exception as e:
        return None, str(e)

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    try:
        target_lang = LANG_MAP.get(payload.lang, "Russian")
        prompt = f"Extract city (English) and write 2-sentence cool greeting in {target_lang} for bstay24. User: {payload.message}. Return JSON: {{\"city\": \"CityName\", \"text\": \"Greeting\"}}"
        
        ai_response = None
        engine = "None"

        # Сначала Groq (как самый стабильный сейчас)
        if groq_keys:
            try:
                g_key = random.choice(groq_keys)
                resp = requests.post("https://api.groq.com/openai/v1/chat/completions", 
                    headers={"Authorization": f"Bearer {g_key}"}, 
                    json={
                        "model": "llama-3.3-70b-versatile",
                        "messages": [{"role": "user", "content": prompt}],
                        "response_format": {"type": "json_object"}
                    }, timeout=10)
                ai_response = resp.json()['choices'][0]['message']['content']
                engine = "Groq"
            except: pass

        if not ai_response:
            return JSONResponse(content={"reply": "Извините, ИИ сейчас перегружен. Попробуйте через минуту."})

        # Обработка ответа
        start = ai_response.find('{')
        end = ai_response.rfind('}') + 1
        data = json.loads(ai_response[start:end])
        city = data.get("city", "none")
        greeting = data.get("text", "Город найден!")

        # Отели
        hotels, err = get_hotels(city, payload.lang)
        hotels_html = ""
        if hotels:
            hotels_html = "<div style='margin-top:15px; display:flex; flex-direction:column; gap:12px;'>"
            for h in hotels:
                name = h.get('hotel_name', 'Hotel')
                price = int(h.get('min_total_price', 0))
                img = h.get('main_photo_url', '').replace('square60', 'square300')
                h_link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(name)}&campaign=ai_card"
                
                hotels_html += f"""
                <div style='background:#fff; border:1px solid #eee; border-radius:10px; overflow:hidden; box-shadow:0 3px 8px rgba(0,0,0,0.1);'>
                    <img src='{img}' style='width:100%; height:140px; object-fit:cover;'>
                    <div style='padding:12px;'>
                        <div style='font-weight:bold; font-size:14px; color:#333;'>{name}</div>
                        <div style='font-size:13px; color:#28a745; margin:5px 0; font-weight:bold;'>от {price} USD за 3 ночи</div>
                        <a href='{h_link}' target='_blank' style='display:block; text-align:center; padding:10px; background:#007BFF; color:white; text-decoration:none; border-radius:6px; font-weight:bold; font-size:12px;'>Выбрать номер</a>
                    </div>
                </div>
                """
            hotels_html += "</div>"

        main_url = f"https://www.stay22.com/allez/{STAY22_AID}?address={city}&link=https://www.booking.com/searchresults.html?ss={city}"
        btn_text = f"🏨 Все варианты в {city}" if payload.lang == 'ru' else f"🏨 All hotels in {city}"
        
        footer = f"""
        <br><a href='{main_url}' target='_blank' style='display:inline-block; padding:15px 25px; background:#003580; color:white; text-decoration:none; border-radius:8px; font-weight:bold; width:100%; text-align:center; box-sizing:border-box;'>{btn_text}</a>
        <br><small style='font-size:9px; color:gray;'>Engine: {engine} | {err if err else "Live Data"}</small>
        """

        return JSONResponse(content={"reply": greeting + hotels_html + footer})

    except Exception as e:
        return JSONResponse(content={"reply": f"Ошибка сервера: {str(e)}"})
