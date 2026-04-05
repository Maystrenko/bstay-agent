import os
import json
import urllib.parse
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

# 1. Настройка ключа и модели
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") 
STAY22_AID = "bstay24"

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
else:
    print("ВНИМАНИЕ: Ключ GEMINI_API_KEY не найден в Vercel!")

# Модель 1.5-flash — самая стабильная для бесплатных аккаунтов (1500/день)
model = genai.GenerativeModel('gemini-2.0-flash')

class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    try:
        # ШАГ 1: Извлекаем город (игнорируем всю историю для чистоты поиска)
        extract_prompt = f"Identify the city in this text: '{payload.message}'. Return ONLY the city name in English. No other words. If no city, say 'none'."
        response = model.generate_content(extract_prompt)
        detected_city = response.text.strip().replace(".", "").split('\n')[0].strip()
        
        if "none" in detected_city.lower() or len(detected_city) < 3:
            return JSONResponse(content={"reply": "Привет! Назовите город, и я найду лучшие отели."})

        # ШАГ 2: Собираем прямую ссылку
        city_encoded = urllib.parse.quote(detected_city)
        booking_url = f"https://www.booking.com/searchresults.html?ss={city_encoded}&lang=ru"
        final_link = f"https://www.stay22.com/allez/{STAY22_AID}?campaign=ai-bot&link={urllib.parse.quote(booking_url)}"
        
        # ШАГ 3: Пишем текст
        answer_prompt = f"Write 2 short sentences in Russian about traveling to {detected_city}. Be inviting."
        final_res = model.generate_content(answer_prompt)
        ai_text = final_res.text.replace("```html", "").replace("```", "").strip()

        button_html = f"""
        <br><br>
        <a href='{final_link}' target='_blank' style='display:inline-block; padding:12px 24px; background:#003580; color:white; text-decoration:none; border-radius:4px; font-weight:bold;'>
           🏨 Посмотреть варианты в {detected_city}
        </a>
        <br><small style='color:gray; font-size:10px;'>Город: {detected_city}</small>
        """
        
        return JSONResponse(
            content={"reply": ai_text + button_html},
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"}
        )
        
    except Exception as e:
        # Если будет ошибка 429 или любая другая - мы увидим её текст в чате
        return JSONResponse(content={"reply": f"Ошибка: {str(e)}"})
