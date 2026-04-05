import os
import json
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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
    # ШАГ 1: Извлекаем ТОЛЬКО город из последнего сообщения
    # Мы вообще не смотрим на историю, чтобы не вспоминать Манчестер
    extract_prompt = f"Extract only the city name from this text: '{payload.message}'. Return only the city name in English. If no city, return 'none'."
    
    try:
        response = model.generate_content(extract_prompt)
        # Чистим ответ от пробелов и точек
        new_city = response.text.strip().replace(".", "")
        
        # Если ИИ не нашел город
        if "none" in new_city.lower() or len(new_city) < 2:
            return {"reply": "Привет! Назовите город, и я подберу лучшие отели."}
            
        # Формируем ссылку СТРОГО под этот новый город
        booking_url = f"https://www.booking.com/searchresults.html?ss={new_city}"
        stay22_link = f"https://www.stay22.com/allez/{STAY22_AID}?campaign=ai-bot&link={booking_url}"
        
        # ШАГ 2: Создаем ответ именно про этот город
        answer_prompt = f"""
        User wants to go to {new_city}. 
        Write a very short greeting (2 sentences) about {new_city} in the user's language. 
        Add this button:
        <br><br><a href='{stay22_link}' target='_blank' style='display:inline-block; padding:12px 24px; background:#007BFF; color:white; text-decoration:none; border-radius:8px; font-weight:bold;'>Посмотреть отели в {new_city}</a>
        Return ONLY HTML.
        """
        
        final_res = model.generate_content(answer_prompt)
        clean_html = final_res.text.replace("```html", "").replace("```", "").strip()
        
        return {"reply": clean_html}
        
    except Exception as e:
        return {"reply": f"Системная ошибка: {str(e)}"}
