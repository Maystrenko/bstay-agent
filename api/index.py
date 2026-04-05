import os
import json
import urllib.parse  # Добавили для правильных ссылок
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
model = genai.GenerativeModel('gemini-2.5-flash')

class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    # ШАГ 1: Извлекаем ТОЛЬКО название города (максимально строго)
    extract_prompt = f"Identify the city in this message: '{payload.message}'. Reply ONLY with the city name in English. If no city, reply 'none'. No dots, no explanations."
    
    try:
        response = model.generate_content(extract_prompt)
        # Очищаем результат от всего лишнего (пробелы, кавычки, точки)
        new_city = response.text.strip().replace(".", "").replace("'", "").replace("\"", "").split('\n')[0].strip()
        
        if "none" in new_city.lower() or len(new_city) < 3:
            return JSONResponse(content={"reply": "Привет! Напишите город, в который планируете поездку."})

        # ШАГ 2: ПРАВИЛЬНО кодируем ссылку, чтобы она не ломалась при переходе
        booking_url = f"https://www.booking.com/searchresults.html?ss={new_city}"
        encoded_url = urllib.parse.quote(booking_url, safe='')
        stay22_link = f"https://www.stay22.com/allez/{STAY22_AID}?campaign=ai-bot&link={encoded_url}"
        
        # ШАГ 3: Создаем ответ. Добавляем проверку города в текст для вас.
        answer_prompt = f"""
        User is going to {new_city}. 
        1. Write a 1-sentence greeting about {new_city} in Russian.
        2. Mention 2 best areas.
        3. Add this EXACT button:
        <br><br><a href='{stay22_link}' target='_blank' style='display:inline-block; padding:12px 24px; background:#007BFF; color:white; text-decoration:none; border-radius:8px; font-weight:bold;'>Посмотреть отели в {new_city}</a>
        <br><small style='color:gray;'>Локация определена как: {new_city}</small>
        Return ONLY HTML. No markdown.
        """
        
        final_res = model.generate_content(answer_prompt)
        clean_html = final_res.text.replace("```html", "").replace("```", "").strip()
        
        return JSONResponse(
            content={"reply": clean_html},
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"}
        )
        
    except Exception as e:
        return {"reply": f"Ошибка: Попробуйте еще раз. ({str(e)})"}
