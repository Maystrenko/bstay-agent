import os
import json
import urllib.parse
import time
import random # Добавили для выбора случайного ключа
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

# 1. ЛОГИКА РОТАЦИИ КЛЮЧЕЙ
raw_keys = os.environ.get("GEMINI_API_KEY", "")
# Разрезаем строку по запятой в список
API_KEYS = [k.strip() for k in raw_keys.split(",") if k.strip()]
STAY22_AID = "bstay24"

MODELS_TO_TRY = ["gemini-1.5-flash-8b", "gemini-1.5-flash", "gemini-2.0-flash-lite"]

class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    try:
        if not API_KEYS:
            return JSONResponse(content={"reply": "Ошибка: Ключи не найдены."})

        # Выбираем случайный ключ из списка для этого запроса
        current_key = random.choice(API_KEYS)
        genai.configure(api_key=current_key)
        
        current_time = str(int(time.time()))
        
        prompt = f"""
        User: "{payload.message}"
        Extract city in English. Write 2-sentence friendly greeting in Russian.
        Return ONLY JSON: {{"city": "CityName", "text": "Russian text"}}
        """
        
        ai_response = ""
        used_model = ""
        
        # Пробуем модели
        for m_name in MODELS_TO_TRY:
            try:
                model = genai.GenerativeModel(m_name)
                res = model.generate_content(prompt)
                if res.text:
                    ai_response = res.text
                    used_model = m_name
                    break
            except Exception as e:
                if "429" in str(e) and len(API_KEYS) > 1:
                    # Если один ключ заблокирован, пробуем другой (рекурсивно или просто ошибку)
                    continue 
                continue

        # Парсинг ответа
        clean_json = ai_response.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_json[clean_json.find('{'):clean_json.rfind('}')+1])
        
        detected_city = data.get("city", "none")
        ai_text = data.get("text", "Готово!")

        # Ссылка Stay22
        booking_url = f"https://www.booking.com/searchresults.html?ss={urllib.parse.quote(detected_city)}&lang=ru"
        params = {
            "campaign": "ai_bot",
            "link": booking_url,
            "address": detected_city,
            "t": current_time
        }
        stay22_link = f"https://www.stay22.com/allez/{STAY22_AID}?{urllib.parse.urlencode(params)}"
        
        button_html = f"""
        <br><br>
        <a href='{stay22_link}' target='_blank' style='display:inline-block; padding:14px 28px; background:#003580; color:white; text-decoration:none; border-radius:4px; font-weight:bold;'>
           🏨 Отели в {detected_city}
        </a>
        """
        
        return JSONResponse(content={"reply": ai_text + button_html})
        
    except Exception as e:
        return JSONResponse(content={"reply": f"Нужно подождать 10 секунд... (Система обновляет лимиты)"})
