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

# Настройка CORS для вашего сайта
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Настройки ключей (берутся из Environment Variables в Vercel)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") 
STAY22_AID = "bstay24"

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# Используем стабильную модель с лимитом 1500 запросов в день
model = genai.GenerativeModel('gemini-flash-latest')

class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    try:
        current_time = str(time.time())
        
        # ШАГ 1: Извлекаем город строго из последнего сообщения (игнорируем историю)
        extract_prompt = f"""
        Extract ONLY the city name in English from this message: '{payload.message}'. 
        Ignore any previous context. If no city, say 'none'. 
        Reply with ONE WORD only.
        """
        response = model.generate_content(extract_prompt)
        detected_city = response.text.strip().split('\n')[0].replace(".", "").replace("'", "").strip()
        
        if "none" in detected_city.lower() or len(detected_city) < 3:
            return JSONResponse(content={"reply": "Привет! В какой город вы ищете отель?"})

        # ШАГ 2: Формируем прямую ссылку на Booking через Stay22
        city_query = urllib.parse.quote(detected_city)
        # Добавляем метку времени &t=, чтобы ссылка всегда была уникальной для браузера
        booking_url = f"https://www.booking.com/searchresults.html?ss={city_query}&lang=ru&t={current_time}"
        final_link = f"https://www.stay22.com/allez/{STAY22_AID}?campaign=ai-bot&link={urllib.parse.quote(booking_url)}"
        
        # ШАГ 3: Текст ответа ИИ
        answer_prompt = f"Напиши 2 коротких предложения на русском языке о поездке в {detected_city}. Будь вежлив."
        final_res = model.generate_content(answer_prompt)
        ai_text = final_res.text.replace("```html", "").replace("```", "").strip()

        # HTML-кнопка
        button_html = f"""
        <br><br>
        <a href='{final_link}' target='_blank' style='display:inline-block; padding:14px 28px; background:#003580; color:white; text-decoration:none; border-radius:4px; font-weight:bold; font-family:Arial,sans-serif;'>
           🏨 Посмотреть отели в {detected_city}
        </a>
        <br><small style='color:gray; font-size:9px;'>Локация: {detected_city}</small>
        """
        
        return JSONResponse(
            content={"reply": ai_text + button_html},
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"}
        )
        
    except Exception as e:
        return JSONResponse(content={"reply": f"Ошибка: {str(e)}"})
