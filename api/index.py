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

# Список моделей для проверки (от самых стабильных к новым)
MODELS_TO_TRY = [
    "gemini-1.5-flash-002", 
    "gemini-1.5-flash", 
    "gemini-2.0-flash-lite", 
    "gemini-2.0-flash", 
    "gemini-flash-latest"
]

class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list

def generate_with_fallback(prompt):
    """Пытается вызвать ИИ, перебирая модели из списка при ошибках 404"""
    last_error = ""
    for model_name in MODELS_TO_TRY:
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(prompt)
            return response.text, model_name
        except Exception as e:
            last_error = str(e)
            if "404" in last_error:
                continue # Пробуем следующую модель
            else:
                raise e # Если ошибка 429 или другая — выходим из цикла
    raise Exception(f"Ни одна модель не ответила. Последняя ошибка: {last_error}")

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    try:
        current_time = str(int(time.time()))
        
        # 1. Извлечение города
        extract_prompt = f"Extract city from: '{payload.message}'. Reply ONLY with 1 word in English. If none, say 'none'."
        city_raw, used_model = generate_with_fallback(extract_prompt)
        detected_city = city_raw.strip().split('\n')[0].replace(".", "").replace("'", "").strip()
        
        if "none" in detected_city.lower() or len(detected_city) < 3:
            return JSONResponse(content={"reply": "Привет! Напишите город, и я найду лучшие отели."})

        # 2. Ссылка Stay22 с защитой от "Манчестера"
        city_encoded = urllib.parse.quote(detected_city)
        booking_url = f"https://www.booking.com/searchresults.html?ss={city_encoded}&lang=ru"
        
        # Мы добавляем address и campaign, чтобы перебить автоматику Stay22
        params = {
            "campaign": "ai_search",
            "link": booking_url,
            "address": detected_city,
            "t": current_time
        }
        stay22_link = f"https://www.stay22.com/allez/{STAY22_AID}?{urllib.parse.urlencode(params)}"
        
        # 3. Текст ответа
        answer_prompt = f"Write 2 short sentences in Russian about visiting {detected_city}."
        ai_text, _ = generate_with_fallback(answer_prompt)

        button_html = f"""
        <br><br>
        <a href='{stay22_link}' target='_blank' style='display:inline-block; padding:14px 28px; background:#003580; color:white; text-decoration:none; border-radius:4px; font-weight:bold; font-family:Arial,sans-serif;'>
           🏨 Отели в {detected_city}
        </a>
        <br><small style='color:gray; font-size:9px;'>Город: {detected_city} | Модель: {used_model}</small>
        """
        
        return JSONResponse(
            content={"reply": ai_text + button_html},
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"}
        )
        
    except Exception as e:
        error_str = str(e)
        if "429" in error_str:
            return JSONResponse(content={"reply": "Слишком много запросов! Подождите 30 секунд (лимит Google Free Tier)."})
        return JSONResponse(content={"reply": f"Системная заминка: {error_str}"})
