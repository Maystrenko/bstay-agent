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

# Пытаемся импортировать SDK Gemini
try:
    from google import genai
    SDK_AVAILABLE = True
except:
    SDK_AVAILABLE = False

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ключи из Vercel
gemini_keys = [k.strip() for k in os.environ.get("GEMINI_API_KEY", "").split(",") if k.strip()]
groq_keys = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")

# Настройки для твоей версии API (booking-com18)
RAPID_HOST = "booking-com18.p.rapidapi.com"
STAY22_AID = "bstay24"
LANG_MAP = {'ru': 'Russian', 'en': 'English', 'de': 'German', 'fr': 'French', 'es': 'Spanish'}

class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list
    lang: str = "en"

def get_hotels_safe(city_name, lang='ru'):
    """Поиск отелей через актуальные эндпоинты booking-com18"""
    if not RAPID_API_KEY: 
        return None, "No API Key"
        
    headers = {
        "X-RapidAPI-Key": RAPID_API_KEY,
        "X-RapidAPI-Host": RAPID_HOST
    }
    
    try:
        # 1. Поиск ID города через stays/auto-complete (как на твоём скрине)
        loc_url = f"https://{RAPID_HOST}/stays/auto-complete"
        loc_res = requests.get(loc_url, headers=headers, params={"query": city_name}, timeout=5)
        
        if loc_res.status_code != 200:
            return None, f"Loc Error {loc_res.status_code}"
            
        # В этой версии данные обычно в ключе ['data']
        loc_data = loc_res.json().get('data', [])
        if not loc_data:
            return None, "City not found"
        
        # Берем ID из первого результата
        dest_id = loc_data[0].get('id') 

        # 2. Даты (на 30 дней вперед)
        checkin = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
        checkout = (datetime.now() + timedelta(days=33)).strftime('%Y-%m-%d')

        # 3. Поиск отелей через stays/search (как на твоём скрине)
        search_url = f"https://{RAPID_HOST}/stays/search"
        params = {
            "id": dest_id,
            "checkinDate": checkin,
            "checkoutDate": checkout,
            "adults": "2",
            "rooms": "1",
            "units": "metric",
            "languagecode": lang,
            "currency_code": "USD"
        }
        
        search_res = requests.get(search_url, headers=headers, params=params, timeout=10)
        
        if search_res.status_code != 200:
            return None, f"Search Error {search_res.status_code}"

        # Парсим список отелей (обычно в data -> hotels)
        data = search_res.json().get('data', {})
        hotels = data.get('hotels', []) or data.get('result', [])
        return hotels[:3], None
        
    except Exception as e:
        return None, f"Sys: {str(e)[:20]}"

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    try:
        target_lang = LANG_MAP.get(payload.lang, "Russian")
        prompt = f"Extract city (English) and write 2-sentence cool greeting in {target_lang}. User: {payload.message}. Return JSON: {{\"city\": \"City\", \"text\": \"Greeting\"}}"
        
        ai_response = None
        engine = "None"

        # 1. Пробуем Groq (самый стабильный)
        if groq_keys:
            try:
                g_key = random.choice(groq_keys)
                r = requests.post("https://api.groq.com/openai/v1/chat/completions", 
                    headers={"Authorization": f"Bearer {g_key}"}, 
                    json={
                        "model": "llama-3.3-70b-versatile",
                        "messages": [{"role": "user", "content": prompt}],
                        "response_format": {"type": "json_object"}
                    }, timeout=10)
                ai_response = r.json()['choices'][0]['message']['content']
                engine = "Groq"
            except: pass

        if not ai_response: return JSONResponse(content={"reply": "ИИ занят, попробуй еще раз."})

        data = json.loads(ai_response[ai_response.find('{'):ai_response.rfind('}')+1])
        city = data.get("city", "none")
        greeting = data.get("text", "Город найден!")

        # Поиск отелей
        hotels_html = ""
        api_status = "Live"
        
        if city.lower() != "none":
            hotels, err = get_hotels_safe(city, payload.lang)
            if hotels:
                hotels_html = "<div style='margin-top:15px; display:flex; flex-direction:column; gap:12px;'>"
                for h in hotels:
                    # Поля в booking-com18
                    name = h.get('name') or h.get('hotel_name', 'Hotel')
                    price_info = h.get('price', {}).get('displayPrice', '0')
                    # Убираем лишние значки из цены если есть
                    price = "".join(filter(str.isdigit, str(price_info))) or "0"
                    
                    img = h.get('mainPhotoUrl') or h.get('main_photo_url', '')
                    
                    h_link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(name)}"
                    
                    hotels_html += f"""
                    <div style='background:#fff; border:1px solid #eee; border-radius:12px; overflow:hidden; box-shadow:0 4px 12px rgba(0,0,0,0.1);'>
                        {f"<img src='{img}' style='width:100%; height:140px; object-fit:cover;'>" if img else ""}
                        <div style='padding:12px;'>
                            <div style='font-weight:bold; font-size:15px; color:#333;'>{name}</div>
                            <div style='font-size:13px; color:#28a745; margin:6px 0; font-weight:bold;'>от {price} USD за 3 ночи</div>
                            <a href='{h_link}' target='_blank' style='display:block; text-align:center; padding:10px; background:#007BFF; color:white; text-decoration:none; border-radius:8px; font-weight:bold; font-size:13px;'>Выбрать номер</a>
                        </div>
                    </div>"""
                hotels_html += "</div>"
            if err: api_status = err

        city_enc = urllib.parse.quote(city)
        main_url = f"https://www.stay22.com/allez/{STAY22_AID}?address={city_enc}&link=https://www.booking.com/searchresults.html?ss={city_enc}%26lang={payload.lang}"
        btn_text = f"🏨 Все отели в {city}" if payload.lang == 'ru' else f"🏨 All hotels in {city}"
        
        footer = f"""
        <br><a href='{main_url}' target='_blank' style='display:inline-block; padding:15px; background:#003580; color:white; text-decoration:none; border-radius:8px; font-weight:bold; width:100%; text-align:center; box-sizing:border-box;'>{btn_text}</a>
        <br><small style='color:gray; font-size:9px;'>Engine: {engine} | API: {api_status}</small>
        """

        return JSONResponse(content={"reply": greeting + hotels_html + footer})

    except Exception as e:
        return JSONResponse(content={"reply": f"Ошибка сервера: {str(e)}"})
