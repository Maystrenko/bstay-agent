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

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") 
STAY22_AID = "bstay24" # Твой ID в Stay22

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    try:
        # 1. Извлекаем город (Строго БЕЗ истории, чтобы забыть Манчестер)
        extract_prompt = f"Extract ONLY the city name in English from: '{payload.message}'. If none, say 'none'."
        response = model.generate_content(extract_prompt)
        detected_city = response.text.strip().replace(".", "").split('\n')[0].strip()
        
        if "none" in detected_city.lower() or len(detected_city) < 3:
            return JSONResponse(content={"reply": "Привет! Назовите город, и я найду лучшие отели."})

        # 2. ФОРМИРУЕМ ПРЯМУЮ ССЫЛКУ ЧЕРЕЗ STAY22 К BOOKING
        # Мы подставляем город в параметр ss (search string)
        city_query = urllib.parse.quote(detected_city)
        
        # Самая стабильная структура ссылки для Stay22 + Booking:
        final_link = f"https://www.stay22.com/allez/{STAY22_AID}?campaign=ai-bot&link=https://www.booking.com/searchresults.html?ss={city_query}"
        
        # 3. Текст ответа
        answer_prompt = f"Write 2 short sentences in Russian about {detected_city}. Be inviting."
        final_res = model.generate_content(answer_prompt)
        ai_text = final_res.text.replace("```html", "").replace("```", "").strip()

        # Кнопка в стиле Booking
        button_html = f"""
        <br><br>
        <a href='{final_link}' target='_blank' style='display:inline-block; padding:12px 24px; background:#003580; color:white; text-decoration:none; border-radius:4px; font-weight:bold; font-family: Arial, sans-serif;'>
           🔵 Посмотреть отели в {detected_city}
        </a>
        <br><br>
        <small style='color:gray; font-size:10px;'>Поиск: {detected_city}</small>
        """
        
        return JSONResponse(
            content={"reply": ai_text + button_html},
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"}
        )
        
    except Exception as e:
        return {"reply": "Произошла ошибка. Повторите запрос города."}
