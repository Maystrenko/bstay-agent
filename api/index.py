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

# ЧИТАЕМ КЛЮЧИ
raw_keys = os.environ.get("GEMINI_API_KEY", "")
API_KEYS = [k.strip() for k in raw_keys.split(",") if k.strip()]
STAY22_AID = "bstay24"

MODELS_TO_TRY = ["gemini-1.5-flash-8b", "gemini-1.5-flash", "gemini-2.0-flash-lite"]

LANG_MAP = {
    'ru': 'Russian', 'en': 'English', 'de': 'German', 'fr': 'French', 'es': 'Spanish'
}

class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list
    lang: str = "en"

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    debug_log = "" # Собираем ошибки сюда
    try:
        if not API_KEYS:
            return JSONResponse(content={"reply": "Критическая ошибка: Ключи API не найдены в настройках Vercel!"})

        # Берем случайный ключ
        selected_key = random.choice(API_KEYS)
        genai.configure(api_key=selected_key)
        
        current_time = str(int(time.time()))
        target_lang = LANG_MAP.get(payload.lang, "English")
        
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
                if res and res.text:
                    ai_response = res.text
                    used_model = m_name
                    break
            except Exception as e:
                debug_log += f"[{m_name} error: {str(e)[:50]}] "
                continue

        if not ai_response:
            return JSONResponse(content={"reply": f"Все модели выдали ошибку. Последние логи: {debug_log}"})

        # ПАРСИНГ JSON
        try:
            # Пытаемся найти границы JSON
            start_idx = ai_response.find('{')
            end_idx = ai_response.rfind('}') + 1
            if start_idx == -1 or end_idx == 0:
                raise ValueError("JSON not found in response")
                
            clean_json = ai_response[start_idx:end_idx]
            data = json.loads(clean_json)
        except Exception as e:
            return JSONResponse(content={"reply": f"ИИ выдал странный ответ. Попробуйте еще раз. (Ошибка парсинга: {str(e)})"})
        
        detected_city = data.get("city", "none")
        ai_text = data.get("text", "Hello!")

        if detected_city.lower() == "none" or len(detected_city) < 3:
            msg = "Напишите, пожалуйста, город." if payload.lang == 'ru' else "Please specify a city."
            return JSONResponse(content={"reply": msg})

        # ССЫЛКА
        city_encoded = urllib.parse.quote(detected_city)
        booking_url = f"https://www.booking.com/searchresults.html?ss={city_encoded}&lang={payload.lang}"
        
        params = {
            "campaign": "ai_bot",
            "link": booking_url,
            "address": detected_city,
            "t": current_time
        }
        stay22_link = f"https://www.stay22.com/allez/{STAY22_AID}?{urllib.parse.urlencode(params)}"
        
        btn_text = {
            'ru': f"🏨 Отели в {detected_city}",
            'en': f"🏨 Hotels in {detected_city}",
            'de': f"🏨 Hotels in {detected_city}",
            'fr': f"🏨 Hôtels à {detected_city}",
            'es': f"🏨 Hoteles en {detected_city}"
        }.get(payload.lang, f"Hotels in {detected_city}")

        button_html = f"""
        <br><br>
        <a href='{stay22_link}' target='_blank' style='display:inline-block; padding:14px 28px; background:#003580; color:white; text-decoration:none; border-radius:6px; font-weight:bold;'>
           {btn_text}
        </a>
        <br><small style='color:gray; font-size:9px;'>Model: {used_model} | Key: {selected_key[:4]}***</small>
        """
        
        return JSONResponse(content={"reply": ai_text + button_html})
        
    except Exception as e:
        return JSONResponse(content={"reply": f"Ошибка системы: {str(e)}"})
