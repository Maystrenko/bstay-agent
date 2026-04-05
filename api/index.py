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
    """Поиск отелей через stays/auto-complete и stays/search"""
    if not RAPID_API_KEY: 
        return None, "No API Key"
        
    headers = {
        "X-RapidAPI-Key": RAPID_API_KEY,
        "X-RapidAPI-Host": RAPID_HOST
    }
    
    try:
        # 1. Поиск ID локации (stays/auto-complete)
        loc_url = f"https://{RAPID_HOST}/stays/auto-complete"
        loc_res = requests.get(loc_url, headers=headers, params={"query": city_name}, timeout=5)
        
        if loc_res.status_code != 200:
            return None, f"Loc Error {loc_res.status_code}"
            
        loc_json = loc_res.json()
        loc_data = loc_json.get('data', [])
        if not loc_data:
            return None, "City not found"
        
        # Берем ID из первого результата. В этой API это обычно поле 'id'
        dest_id = loc_data[0].get('id')
        if not dest_id:
            return None, "No ID in data"

        # 2. Даты (на 30 дней вперед)
        checkin = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
        checkout = (datetime.now() + timedelta(days=33)).strftime('%Y-%m-%d')

        # 3. Поиск отелей (stays/search)
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

        search_json = search_res.json()
        # В booking-com18 отели лежат в data -> results или data -> hotels
        data_block = search_json.get('data', {})
        hotels = data_block.get('hotels', []) or data_block.get('results', [])
        
        return hotels[:3], None
        
    except Exception as e:
        return None, f"Sys: {str(e)[:20]}"

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    try:
        target_lang = LANG_MAP.get(payload.lang, "Russian")
        # Улучшаем промпт, чтобы ИИ был более дружелюбным
        prompt = f"""
        You are a travel expert for bstay24.com.
        User message: "{payload.message}"
        1. Extract the city in English.
        2. Write a 2-sentence inspiring greeting in {target_lang} about this city.
        Return ONLY JSON: {{"city": "CityName", "text": "Greeting text"}}
        """
        
        ai_response = None
        engine = "None"

        # Пробуем Groq (самый стабильный по твоим скринам)
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

        if not ai_response: 
            return JSONResponse(content={"reply": "ИИ временно недоступен. Попробуйте еще раз через минуту."})

        # Парсинг ответа ИИ
        try:
            data = json.loads(ai_response[ai_response.find('{'):ai_response.rfind('}')+1])
            city = data.get("city", "none")
            greeting = data.get("text", "Нашел отличные варианты!")
        except:
            return JSONResponse(content={"reply": "Ошибка обработки города. Попробуйте написать название города еще раз."})

        # Поиск отелей
        hotels_html = ""
        api_info = "Live"
        
        if city.lower() != "none":
            hotels, err = get_hotels_safe(city, payload.lang)
            if hotels:
                hotels_html = "<div style='margin-top:15px; display:flex; flex-direction:column; gap:12px;'>"
                for h in hotels:
                    # Поля в booking-com18 могут называться по-разному
                    name = h.get('name') or h.get('hotel_name', 'Hotel')
                    
                    # Цена часто в объекте price -> displayPrice
                    price_data = h.get('price', {})
                    price_val = price_data.get('displayPrice') or h.get('min_total_price', '0')
                    # Очищаем цену от валюты для отображения
                    price = "".join(filter(str.isdigit, str(price_val))) or "0"
                    
                    img = h.get('mainPhotoUrl') or h.get('main_photo_url', '')
                    
                    link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(name)}&campaign=ai_card"
                    
                    hotels_html += f"""
                    <div style='background:#fff; border:1px solid #eee; border-radius:12px; overflow:hidden; box-shadow:0 4px 12px rgba(0,0,0,0.1);'>
                        {f"<img src='{img}' style='width:100%; height:140px; object-fit:cover;'>" if img else ""}
                        <div style='padding:12px;'>
                            <div style='font-weight:bold; font-size:15px; color:#333;'>{name}</div>
                            <div style='font-size:13px; color:#28a745; margin:6px 0; font-weight:bold;'>от {price} USD за 3 ночи</div>
                            <a href='{link}' target='_blank' style='display:block; text-align:center; padding:10px; background:#007BFF; color:white; text-decoration:none; border-radius:8px; font-weight:bold; font-size:13px;'>Выбрать номер</a>
                        </div>
                    </div>"""
                hotels_html += "</div>"
            if err: api_info = err

        # Финальные кнопки
        city_enc = urllib.parse.quote(city)
        main_url = f"https://www.stay22.com/allez/{STAY22_AID}?address={city_enc}&link=https://www.booking.com/searchresults.html?ss={city_enc}%26lang={payload.lang}"
        btn_text = f"🏨 Все отели в {city}" if payload.lang == 'ru' else f"🏨 View hotels in {city}"
        
        footer = f"""
        <br><a href='{main_url}' target='_blank' style='display:inline-block; padding:15px; background:#003580; color:white; text-decoration:none; border-radius:8px; font-weight:bold; width:100%; text-align:center; box-sizing:border-box;'>{btn_text}</a>
        <br><small style='color:gray; font-size:9px;'>Engine: {engine} | API: {api_info}</small>
        """

        return JSONResponse(content={"reply": greeting + hotels_html + footer})

    except Exception as e:
        return JSONResponse(content={"reply": f"Ошибка системы: {str(e)}"})
