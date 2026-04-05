import os
import json
import urllib.parse
import time
import random
import requests
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from google import genai

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# КЛЮЧИ ИЗ VERCEL
gemini_keys = [k.strip() for k in os.environ.get("GEMINI_API_KEY", "").split(",") if k.strip()]
groq_keys = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]
RAPID_API_KEY = os.environ.get("RAPID_API_KEY") 

STAY22_AID = "bstay24"
LANG_MAP = {'ru': 'Russian', 'en': 'English', 'de': 'German', 'fr': 'French', 'es': 'Spanish'}

class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list
    lang: str = "en"

# --- ФУНКЦИЯ ПОИСКА ОТЕЛЕЙ (RapidAPI) ---
def get_hotels(city_name, lang='ru'):
    if not RAPID_API_KEY: return None
    
    headers = {
        "X-RapidAPI-Key": RAPID_API_KEY,
        "X-RapidAPI-Host": "booking-com.p.rapidapi.com"
    }
    
    try:
        # 1. Получаем ID локации
        loc_url = "https://booking-com.p.rapidapi.com/v1/hotels/locations"
        loc_res = requests.get(loc_url, headers=headers, params={"name": city_name, "locale": "en-gb"}, timeout=5)
        locations = loc_res.json()
        if not locations: return None
        
        dest_id = locations[0]['dest_id']
        dest_type = locations[0]['dest_type']

        # 2. Ищем отели
        search_url = "https://booking-com.p.rapidapi.com/v1/hotels/search"
        params = {
            "dest_id": dest_id, "dest_type": dest_type,
            "room_number": "1", "adults_number": "2",
            "order_by": "popularity", "units": "metric", "locale": lang
        }
        search_res = requests.get(search_url, headers=headers, params=params, timeout=5)
        return search_res.json().get('result', [])[:3] # Топ-3
    except:
        return None

# --- ГЛАВНЫЙ ОБРАБОТЧИК ---
@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    try:
        current_time = str(int(time.time()))
        target_lang = LANG_MAP.get(payload.lang, "English")
        
        prompt = f"""
        Analyze: "{payload.message}"
        Return ONLY JSON: {{"city": "CityNameInEnglish", "text": "2-sentence greeting in {target_lang}"}}
        """
        
        ai_response = None
        engine = ""

        # 1. ПРОБУЕМ GEMINI
        if gemini_keys:
            random.shuffle(gemini_keys)
            for k in gemini_keys:
                try:
                    client = genai.Client(api_key=k)
                    res = client.models.generate_content(model='gemini-2.0-flash-lite', contents=prompt)
                    if res.text:
                        ai_response = res.text
                        engine = "Gemini"
                        break
                except: continue

        # 2. ПРОБУЕМ GROQ (Fallback)
        if not ai_response and groq_keys:
            try:
                g_key = random.choice(groq_keys)
                url = "https://api.groq.com/openai/v1/chat/completions"
                resp = requests.post(url, headers={"Authorization": f"Bearer {g_key}"}, json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"}
                }, timeout=10)
                ai_response = resp.json()['choices'][0]['message']['content']
                engine = "Groq"
            except: pass

        if not ai_response:
            return JSONResponse(content={"reply": "AI error. Try again."})

        # ПАРСИНГ И ОТКЛИК
        data = json.loads(ai_response[ai_response.find('{'):ai_response.rfind('}')+1])
        city = data.get("city", "none")
        greeting = data.get("text", "Searching...")

        if city.lower() == "none":
            return JSONResponse(content={"reply": greeting})

        # --- РАБОТА С ОТЕЛЯМИ ---
        hotels_html = ""
        hotels = get_hotels(city, payload.lang)
        
        if hotels:
            hotels_html = "<div style='margin-top:15px; display:flex; flex-direction:column; gap:8px;'>"
            for h in hotels:
                name = h.get('hotel_name', 'Hotel')
                price = h.get('min_total_price', '?')
                curr = h.get('currency_code', 'USD')
                rating = h.get('review_score', '8.0')
                
                # Ссылка Stay22 на конкретный отель
                h_link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(name)}&campaign=hotel_card"
                
                hotels_html += f"""
                <div style='background:#fff; border:1px solid #eee; padding:10px; border-radius:8px; font-size:13px; box-shadow:0 2px 4px rgba(0,0,0,0.05);'>
                    <div style='font-weight:bold; color:#333;'>{name}</div>
                    <div style='color:#666; margin:4px 0;'>⭐ {rating} | {price} {curr}</div>
                    <a href='{h_link}' target='_blank' style='color:#007BFF; text-decoration:none; font-weight:bold;'>Забронировать →</a>
                </div>
                """
            hotels_html += "</div>"

        # Финальная общая кнопка
        city_enc = urllib.parse.quote(city)
        main_link = f"https://www.stay22.com/allez/{STAY22_AID}?address={city_enc}&link=https://www.booking.com/searchresults.html?ss={city_enc}%26lang={payload.lang}"
        
        btn_text = {'ru': f"🏨 Все отели в {city}", 'en': f"🏨 All hotels in {city}"}.get(payload.lang, "Hotels")
        
        button_html = f"""
        <br><a href='{main_link}' target='_blank' style='display:inline-block; padding:12px 24px; background:#003580; color:white; text-decoration:none; border-radius:6px; font-weight:bold;'>{btn_text}</a>
        <br><small style='font-size:9px; color:gray;'>Engine: {engine} | Data: Booking API</small>
        """

        return JSONResponse(content={"reply": greeting + hotels_html + button_html})

    except Exception as e:
        return JSONResponse(content={"reply": f"System busy ({str(e)})"})
