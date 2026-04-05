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

# Разрешаем вашему сайту bstay24.com обращаться к серверу
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Загружаем настройки
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") 
STAY22_AID = "bstay24"

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# Используем gemini-flash-latest (стабильные 1500 запросов в день)
model = genai.GenerativeModel('gemini-flash-latest')

class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    try:
        current_time = str(int(time.time()))
        
        # ШАГ 1: Извлекаем город (игнорируем историю для точности)
        extract_prompt = f"Extract ONLY the city name in English from: '{payload.message}'. One word only. If none, say 'none'."
        response = model.generate_content(extract_prompt)
        detected_city = response.text.strip().split('\n')[0].replace(".", "").replace("'", "").strip()
        
        if "none" in detected_city.lower() or len(detected_city) < 3:
            return JSONResponse(content={"reply": "Привет! Назовите город, в котором вы ищете жилье?"})

        # ШАГ 2: Формируем "бронебойную" ссылку Stay22
        # Мы добавляем параметр 'address', чтобы Stay22 не подставлял Манчестер автоматически
        booking_url = f"https://www.booking.com/searchresults.html?ss={urllib.parse.quote(detected_city)}&lang=ru"
        
        params = {
            "campaign": "ai-bot",
            "link": booking_url,
            "address": detected_city, # Принудительный город
            "t": current_time         # Уникальный ID для сброса кеша
        }
        
        stay22_link = f"https://www.stay22.com/allez/{STAY22_AID}?{urllib.parse.urlencode(params)}"
        
        # ШАГ 3: Пишем ответ пользователю
        answer_prompt = f"Write 2 short friendly sentences in Russian about visiting {detected_city}. No markdown."
        final_res = model.generate_content(answer_prompt)
        ai_text = final_res.text.strip()

        # HTML-кнопка в стиле вашего бренда
        button_html = f"""
        <br><br>
        <a href='{stay22_link}' target='_blank' style='display:inline-block; padding:14px 28px; background:#003580; color:white; text-decoration:none; border-radius:6px; font-weight:bold; font-family:Arial,sans-serif; box-shadow: 0 2px 5px rgba(0,0,0,0.2);'>
           🏨 Найти отели в {detected_city}
        </a>
        <br><small style='color:gray; font-size:9px;'>Маршрут: {detected_city} | bstay24</small>
        """
        
        return JSONResponse(
            content={"reply": ai_text + button_html},
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache"
            }
        )
        
    except Exception as e:
        return JSONResponse(content={"reply": f"Извините, произошла техническая заминка. Попробуйте еще раз. ({str(e)})"})
