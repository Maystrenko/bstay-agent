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

# КЛЮЧИ
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

# --- УЛУЧШЕННЫЙ ПОИСК ОТЕЛЕЙ ---
def get_hotels(city_name, lang='ru'):
    if not RAPID_API_KEY: return None, "No RapidAPI Key"
    
    headers = {
        "X-RapidAPI-Key": RAPID_API_KEY,
        "X-RapidAPI-Host": "booking-com.p.rapidapi.com"
    }
    
    try:
        # 1. Получаем ID локации
        loc_res = requests.get(
            "https://booking-com.p.rapidapi.com/v1/hotels/locations",
            headers=headers,
            params={"name": city_name, "locale": "en-gb"},
            timeout=7
        )
        locations = loc_res.json()
        if not locations or 'dest_id' not in locations[0]:
            return None, "City ID not found"
        
        dest_id = locations[0]['dest_id']
        dest_type = locations[0]['dest_type']

        # 2. Ищем отели (минимум параметров для стабильности)
        search_params = {
            "dest_id": dest_id,
            "dest_type": dest_type,
            "room_number": "1",
            "adults_number": "2",
            "order_by": "popularity",
            "units": "metric",
            "locale": lang,
            "filter_by_currency": "USD"
        }
        
        search_res = requests.get(
            "https://booking-com.p.rapidapi.com/v1/hotels/search",
            headers=headers,
            params=search_params,
            timeout=7
        )
        results = search_res.json().get('result', [])
        return results[:3], None # Возвращаем топ-3
    except Exception as e:
        return None, str(e)

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    try:
        current_time = str(int(time.time()))
        target_lang = LANG_MAP.get(payload.lang, "English")
        
        prompt = f"Extract city (English) and write 2-sentence greeting ({target_lang}) from: {payload.message}. Return JSON: {{\"city\": \"City\", \"text\": \"Greeting\"}}"
        
        ai_response = None
        engine = ""

        # Пробуем ИИ (Gemini -> Groq)
        # ... (код вызова Gemini/Groq оставляем прежним, он у вас работает)
        # Для примера возьмем Groq как в вашем скриншоте
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

        if not ai_response: return JSONResponse(content={"reply": "AI Error"})

        data = json.loads(ai_response[ai_response.find('{'):ai_response.rfind('}')+1])
        city = data.get("city", "none")
        greeting = data.get("text", "City found!")

        # Пытаемся получить отели
        hotels, api_error = get_hotels(city, payload.lang)
        
        hotels_html = ""
        if hotels:
            hotels_html = "<div style='margin-top:15px; display:flex; flex-direction:column; gap:12px;'>"
            for h in hotels:
                name = h.get('hotel_name', 'Hotel')
                price = h.get('min_total_price', '?')
                curr = h.get('currency_code', 'USD')
                score = h.get('review_score', '8.0')
                img = h.get('main_photo_url', '').replace('square60', 'square300') # Улучшаем качество фото
                
                link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(name)}&campaign=ai_card"
                
                hotels_html += f"""
                <div style='background:#fff; border:1px solid #eee; border-radius:10px; overflow:hidden; box-shadow:0 3px 6px rgba(0,0,0,0.08);'>
                    {f"<img src='{img}' style='width:100%; height:120px; object-fit:cover;'>" if img else ""}
                    <div style='padding:10px;'>
                        <div style='font-weight:bold; font-size:14px; color:#333; margin-bottom:4px;'>{name}</div>
                        <div style='font-size:12px; color:#666;'>⭐ {score} | {price} {curr}</div>
                        <a href='{link}' target='_blank' style='display:block; margin-top:8px; text-align:center; padding:8px; background:#007BFF; color:white; text-decoration:none; border-radius:5px; font-weight:bold; font-size:12px;'>Забронировать</a>
                    </div>
                </div>
                """
            hotels_html += "</div>"

        # Кнопка поиска
        city_enc = urllib.parse.quote(city)
        main_url = f"https://www.stay22.com/allez/{STAY22_AID}?address={city_enc}&link=https://www.booking.com/searchresults.html?ss={city_enc}%26lang={payload.lang}"
        
        btn_text = "Все отели в " + city if payload.lang == 'ru' else "All hotels in " + city
        
        footer_btn = f"""
        <br><a href='{main_url}' target='_blank' style='display:inline-block; padding:14px 24px; background:#003580; color:white; text-decoration:none; border-radius:6px; font-weight:bold; width:100%; box-sizing:border-box; text-align:center;'>🏨 {btn_text}</a>
        <br><small style='font-size:9px; color:gray;'>Engine: {engine} | Data: {api_error if api_error else "Booking API"}</small>
        """

        return JSONResponse(content={"reply": greeting + hotels_html + footer_btn})

    except Exception as e:
        return JSONResponse(content={"reply": f"System error: {str(e)}"})
