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
    # ШАГ 1: Анализ города БЕЗ истории чата (чтобы не тянуть Манчестер)
    intent_prompt = f"""
    Проанализируй только это сообщение: "{payload.message}"
    Если в нем есть название города или страны, верни СТРОГО JSON: {{"status": "search", "city": "City name in English"}}
    Если города нет, верни JSON: {{"status": "chat", "reply": "Твой ответ"}}
    Игнорируй любые предыдущие города.
    """
    
    try:
        response = model.generate_content(intent_prompt)
        res_text = response.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(res_text[res_text.find('{'):res_text.rfind('}')+1])
        
        # Если это просто разговор
        if data.get("status") == "chat":
            return {"reply": data["reply"].replace("```html", "").replace("```", "").strip()}
            
        # Если найден НОВЫЙ город
        new_city = data.get("city")
        
        # Генерируем ссылку СТРОГО под этот город
        # ss={new_city} — это главный параметр для Booking
        booking_url = f"https://www.booking.com/searchresults.html?ss={new_city}"
        stay22_link = f"https://www.stay22.com/allez/{STAY22_AID}?campaign=ai-bot&link={booking_url}"
        
        # ШАГ 2: Пишем ответ именно про этот город
        final_prompt = f"""
        Напиши краткий привет в 2 предложениях для тех, кто едет в {new_city}. 
        Посоветуй 2 крутых района. 
        Заверши ответ ЭТОЙ кнопкой:
        <br><br><a href='{stay22_link}' target='_blank' style='display:inline-block; padding:12px 24px; background:#007BFF; color:white; text-decoration:none; border-radius:8px; font-weight:bold;'>Посмотреть отели в {new_city}</a>
        Верни ТОЛЬКО HTML без Markdown.
        """
        
        final_res = model.generate_content(final_prompt)
        return {"reply": final_res.text.replace("```html", "").replace("```", "").strip()}
        
    except Exception as e:
        return {"reply": "Упс! Попробуйте написать название города еще раз."}
