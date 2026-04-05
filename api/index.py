import os
import json
import urllib.parse
import time  # Добавили для уникальности запросов
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
# Используем самую стабильную модель
model = genai.GenerativeModel('gemini-flash-latest')

class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    try:
        # --- СУПЕР-ЖЕСТКАЯ ИНСТРУКЦИЯ ---
        # Мы добавляем текущее время (timestamp) в промпт, чтобы ИИ всегда видел новый текст
        current_time = str(time.time())
        extract_prompt = f"""
        Timestamp: {current_time}
        Task: Extract ONLY the city name in English from the user message. 
        Message: "{payload.message}"
        IMPORTANT: Ignore all previous cities or contexts. Focus ONLY on the message above.
        If no city found, reply 'none'.
        Reply with ONE WORD ONLY.
        """
        
        response = model.generate_content(extract_prompt)
        # Очистка: берем только первое слово, убираем знаки препинания
        detected_city = response.text.strip().split()[0].replace(".", "").replace(",", "").replace("'", "")
        
        if "none" in detected_city.lower() or len(detected_city) < 3:
            return JSONResponse(content={"reply": "Привет! Напишите название города, чтобы я нашел отели."})

        # Формируем ссылку
        city_encoded = urllib.parse.quote(detected_city)
        # Добавляем в ссылку случайный параметр &t=..., чтобы Букинг и Vercel не кешировали её
        booking_url = f"https://www.booking.com/searchresults.html?ss={city_encoded}&lang=ru&t={current_time}"
        final_link = f"https://www.stay22.com/allez/{STAY22_AID}?campaign=ai-bot&link={urllib.parse.quote(booking_url)}"
        
        # Генерация текста ответа
        answer_prompt = f"Write 2 short friendly sentences in Russian about {detected_city}. No markdown."
        final_res = model.generate_content(answer_prompt)
        ai_text = final_res.text.replace("```html", "").replace("```", "").strip()

        button_html = f"""
        <br><br>
        <a href='{final_link}' target='_blank' style='display:inline-block; padding:14px 28px; background:#003580; color:white; text-decoration:none; border-radius:4px; font-weight:bold;'>
           🏨 Посмотреть отели в {detected_city}
        </a>
        <br><small style='color:gray; font-size:9px;'>ID поиска: {current_time[-5:]} | Город: {detected_city}</small>
        """
        
        # Возвращаем ответ с ЖЕСТКИМ запретом на кеширование
        return JSONResponse(
            content={"reply": ai_text + button_html},
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache"
            }
        )
        
    except Exception as e:
        return JSONResponse(content={"reply": f"Ошибка: {str(e)}"})
