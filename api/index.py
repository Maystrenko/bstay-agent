import os
import json
import urllib.parse
import time
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

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") 
STAY22_AID = "bstay24"

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# ИСПОЛЬЗУЕМ 1.5-FLASH-8B — она самая живучая для бесплатных ключей
MODEL_NAME = 'gemini-1.5-flash-8b'
model = genai.GenerativeModel(MODEL_NAME)

class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    try:
        current_time = str(int(time.time()))
        
        # 1. Извлечение города (максимально жестко)
        extract_prompt = f"Identify the city in this text: '{payload.message}'. Reply ONLY with the city name in English. No dots. No context. If no city, say 'none'."
        
        # Делаем запрос к ИИ
        response = model.generate_content(extract_prompt)
        
        if not response.text:
            raise ValueError("Пустой ответ от модели")

        detected_city = response.text.strip().split('\n')[0].replace(".", "").replace("'", "").strip()
        
        if "none" in detected_city.lower() or len(detected_city) < 3:
            return JSONResponse(content={"reply": "Привет! Напишите город, и я подберу отели."})

        # 2. Формируем "бронебойную" ссылку Stay22
        city_encoded = urllib.parse.quote(detected_city)
        booking_url = f"https://www.booking.com/searchresults.html?ss={city_encoded}&lang=ru"
        
        params = {
            "campaign": "ai-bot",
            "link": booking_url,
            "address": detected_city, # Защита от "Манчестера"
            "t": current_time
        }
        
        stay22_link = f"https://www.stay22.com/allez/{STAY22_AID}?{urllib.parse.urlencode(params)}"
        
        # 3. Текст ответа
        answer_prompt = f"Напиши 2 очень коротких предложения про отдых в {detected_city}. Без лишних слов."
        final_res = model.generate_content(answer_prompt)
        ai_text = final_res.text.strip()

        button_html = f"""
        <br><br>
        <a href='{stay22_link}' target='_blank' style='display:inline-block; padding:14px 28px; background:#003580; color:white; text-decoration:none; border-radius:4px; font-weight:bold; font-family:Arial, sans-serif;'>
           🏨 Отели в {detected_city} на Booking
        </a>
        <br><small style='color:gray; font-size:9px;'>Локация: {detected_city} (через {MODEL_NAME})</small>
        """
        
        return JSONResponse(
            content={"reply": ai_text + button_html},
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"}
        )
        
    except Exception as e:
        error_msg = str(e)
        # Если это опять ошибка лимита или 404, мы увидим подробности
        return JSONResponse(content={"reply": f"Системное уведомление: {error_msg}. Пожалуйста, попробуйте через 15 секунд."})
