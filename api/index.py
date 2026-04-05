import os
import json
import urllib.parse
import time
import random
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import google.generativeai as genai

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Берем список ключей (в Vercel вставляй их через запятую: key1,key2,key3)
raw_keys = os.environ.get("GEMINI_API_KEY", "")
API_KEYS = [k.strip() for k in raw_keys.split(",") if k.strip()]
STAY22_AID = "bstay24"

MODELS_TO_TRY = ["gemini-1.5-flash-8b", "gemini-1.5-flash", "gemini-2.0-flash-lite"]

# Словарь для превращения кода языка в полное название для ИИ
LANG_MAP = {
    'ru': 'Russian',
    'en': 'English',
    'de': 'German',
    'fr': 'French',
    'es': 'Spanish'
}

class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list
    lang: str = "en" # <-- Принимаем язык (по умолчанию английский)

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    try:
        if not API_KEYS:
            return JSONResponse(content={"reply": "API Key missing"})

        # Ротация ключей: выбираем случайный
        genai.configure(api_key=random.choice(API_KEYS))
        
        current_time = str(int(time.time()))
        target_lang = LANG_MAP.get(payload.lang, "English") # Определяем язык для ответа
        
        # Обновленный промпт, который учитывает язык пользователя
        prompt = f"""
        User message: "{payload.message}"
        1. Extract the destination city in English.
        2. Write a 2-sentence friendly greeting in {target_lang} about this city.
        Return ONLY JSON: {{"city": "CityName", "text": "Greeting text"}}
        """
        
        ai_response = ""
        used_model = ""
        
        for m_name in MODELS_TO_TRY:
            try:
                model = genai.GenerativeModel(m_name)
                res = model.generate_content(prompt)
                if res.text:
                    ai_response = res.text
                    used_model = m_name
                    break
            except:
                continue

        # Парсинг ответа
        clean_json = ai_response.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_json[clean_json.find('{'):clean_json.rfind('}')+1])
        
        detected_city = data.get("city", "none")
        ai_text = data.get("text", "Hello!")

        if detected_city.lower() == "none":
            return JSONResponse(content={"reply": "Please specify a city." if payload.lang == 'en' else "Уточните город, пожалуйста."})

        # Ссылка Stay22 (включая защиту от Манчестера)
        city_encoded = urllib.parse.quote(detected_city)
        booking_url = f"https://www.booking.com/searchresults.html?ss={city_encoded}&lang={payload.lang}"
        
        params = {
            "campaign": "ai_bot",
            "link": booking_url,
            "address": detected_city,
            "t": current_time
        }
        stay22_link = f"https://www.stay22.com/allez/{STAY22_AID}?{urllib.parse.urlencode(params)}"
        
        # Текст на кнопке тоже можно сделать мультиязычным
        btn_text = {
            'ru': f"🏨 Отели в {detected_city}",
            'en': f"🏨 Hotels in {detected_city}",
            'de': f"🏨 Hotels в {detected_city}",
            'fr': f"🏨 Hôtels à {detected_city}",
            'es': f"🏨 Hoteles en {detected_city}"
        }.get(payload.lang, f"Hotels in {detected_city}")

        button_html = f"""
        <br><br>
        <a href='{stay22_link}' target='_blank' style='display:inline-block; padding:14px 28px; background:#003580; color:white; text-decoration:none; border-radius:6px; font-weight:bold; font-family:Arial,sans-serif;'>
           {btn_text}
        </a>
        <br><small style='color:gray; font-size:9px;'>ID: {current_time[-4:]} | {used_model}</small>
        """
        
        return JSONResponse(content={"reply": ai_text + button_html})
        
    except Exception as e:
        return JSONResponse(content={"reply": "Service temporarily busy, please try again in 10 seconds."})
