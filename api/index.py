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
STAY22_AID = "btr"

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash')

class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    # УЛУЧШЕННАЯ ИНСТРУКЦИЯ: Заставляем ИИ всегда проверять наличие НОВОГО города
    system_instructions = """
    Ты - тур-агент bstay24. Твоя главная задача: определить, какой город пользователь ищет ПРЯМО СЕЙЧАС.
    Игнорируй предыдущие города из истории, если в новом сообщении указан другой город.
    Если есть новый город: верни ТОЛЬКО JSON {"status": "search", "city": "City Name in English"}
    Если города нет и это просто беседа: верни JSON {"status": "chat", "reply": "Текст ответа"}
    """
    
    # Мы передаем только последнее сообщение для анализа города, чтобы не было путаницы с Манчестером
    analysis_prompt = f"{system_instructions}\nСообщение пользователя: {payload.message}"
    
    try:
        response = model.generate_content(analysis_prompt)
        result_text = response.text.replace("```json", "").replace("```", "").strip()
        start = result_text.find('{')
        end = result_text.rfind('}') + 1
        data = json.loads(result_text[start:end])
        
        if data.get("status") == "chat":
            return {"reply": data["reply"].replace("```html", "").replace("```", "").strip()}
            
        city = data.get("city")
        
        # Генерируем ссылку строго под новый город
        booking_url = f"https://www.booking.com/searchresults.html?ss={city}"
        stay22_link = f"https://www.stay22.com/allez/{STAY22_AID}?campaign=ai-bot&link={booking_url}"
        
        # Формируем ответ именно про НОВЫЙ город
        answer_prompt = f"""
        Напиши кратко (2 предложения) про отдых в {city}. 
        Посоветуй 2 района.
        В конце добавь кнопку (не меняй код):
        <br><br><a href='{stay22_link}' target='_blank' style='display:inline-block; padding:12px 24px; background:#007BFF; color:white; text-decoration:none; border-radius:8px; font-weight:bold;'>Посмотреть отели в {city}</a>
        Верни ТОЛЬКО HTML. Без Markdown.
        """
        
        final_response = model.generate_content(answer_prompt)
        return {"reply": final_response.text.replace("```html", "").replace("```", "").strip()}
        
    except Exception as e:
        return {"reply": f"Ошибка: {str(e)}"}
