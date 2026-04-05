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

# Берем ключ из настроек Vercel
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") 
STAY22_AID = "bstay24"

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# СТАВИМ МОДЕЛЬ 1.5 FLASH (1500 запросов в день бесплатно)
model = genai.GenerativeModel('gemini-1.5-flash')

class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    try:
        # 1. Извлечение города (без истории)
        extract_prompt = f"Identify the destination city from: '{payload.message}'. Reply ONLY with the city name in English. No context. If no city, reply 'none'."
        response = model.generate_content(extract_prompt)
        detected_city = response.text.strip().split('\n')[0].replace(".", "").replace("'", "").strip()
        
        if "none" in detected_city.lower() or len(detected_city) < 3:
            return JSONResponse(content={"reply": "Привет! В какой город едем? Напишите название."})

        # 2. Ссылка (Прямой Букинг через Stay22)
        city_encoded = urllib.parse.quote(detected_city)
        booking_url = f"https://www.booking.com/searchresults.html?ss={city_encoded}&lang=ru"
        final_link = f"https://www.stay22.com/allez/{STAY22_AID}?campaign=ai-bot&link={urllib.parse.quote(booking_url)}"
        
        # 3. Текст ответа
        answer_prompt = f"Write 2 short sentences in Russian about visiting {detected_city}. Be very brief."
        final_res = model.generate_content(answer_prompt)
        ai_text = final_res.text.replace("```html", "").replace("```", "").strip()

        button_html = f"""
        <br><br>
        <a href='{final_link}' target='_blank' style='display:inline-block; padding:12px 24px; background:#003580; color:white; text-decoration:none; border-radius:4px; font-weight:bold;'>
           🏨 Отели в {detected_city} на Booking
        </a>
        <br><small style='color:gray; font-size:9px;'>Город: {detected_city}</small>
        """
        
        return JSONResponse(
            content={"reply": ai_text + button_html},
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"}
        )
        
    except Exception as e:
        # ВРЕМЕННО: выводим реальную ошибку, чтобы понять причину
        return {"reply": f"Техническая ошибка: {str(e)}"}
