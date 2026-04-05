import os
import json
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import google.generativeai as genai

app = FastAPI()

# Разрешаем вашему сайту bstay24.com обращаться к этому серверу
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- НАСТРОЙКИ КЛЮЧЕЙ ---
# Код берет ключ из "Environment Variables" на Vercel, чтобы его не украли
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") 
STAY22_AID = "btr"

if not GEMINI_API_KEY:
    # Эта ошибка появится только в логах Vercel, если вы забыли добавить ключ
    print("КРИТИЧЕСКАЯ ОШИБКА: GEMINI_API_KEY не найден в переменных окружения!")
else:
    genai.configure(api_key=GEMINI_API_KEY)

# Используем самую быструю модель из вашего списка
model = genai.GenerativeModel('gemini-2.5-flash')

class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    # 1. Инструкция для ИИ: Как распознать город
    system_instructions = """
    Ты - умный ИИ-агент туристического сервиса bstay24. Твоя задача: понять, какой город ищет пользователь.
    Если пользователь называет локацию (город, страну), верни СТРОГО валидный JSON: {"status": "search", "city": "Название города на английском"}
    Если город не указан, пользователь здоровается или говорит на отвлеченные темы, верни JSON: {"status": "chat", "reply": "Твой ответ пользователю (в HTML)"}
    """
    
    prompt = f"{system_instructions}\nЗапрос пользователя: {payload.message}"
    
    try:
        # Запрос к ИИ для определения намерения
        response = model.generate_content(prompt)
        
        # Очистка ответа от возможных ```json или ``` блоков
        result_text = response.text.replace("```json", "").replace("```", "").strip()
        start = result_text.find('{')
        end = result_text.rfind('}') + 1
        clean_json = result_text[start:end]
        data = json.loads(clean_json)
        
        # Если это просто разговор
        if data.get("status") == "chat":
            clean_reply = data["reply"].replace("```html", "").replace("```", "").strip()
            return {"reply": clean_reply}
            
        # Если найден город
        city = data.get("city")
        
        # Создаем вашу партнерскую ссылку
        booking_url = f"https://www.booking.com/searchresults.html?ss={city}"
        stay22_link = f"https://www.stay22.com/allez/{STAY22_AID}?campaign=ai-bot&link={booking_url}"
        
        # 2. Просим ИИ написать красивый текст с кнопкой
        answer_prompt = f"""
        Пользователь ищет отели в: {city}.
        Напиши очень краткий приветливый текст на языке пользователя. 
        Посоветуй 2 лучших района этого города для туристов.
        В конце ОБЯЗАТЕЛЬНО добавь эту кнопку:
        <br><br><a href='{stay22_link}' target='_blank' style='display:inline-block; padding:12px 24px; background:#007BFF; color:white; text-decoration:none; border-radius:8px; font-weight:bold; box-shadow: 0 4px 6px rgba(0,0,0,0.1);'>Посмотреть отели и цены в {city}</a>
        Верни ТОЛЬКО чистый текст. Не используй ```html или другие Markdown символы.
        """
        
        final_response = model.generate_content(answer_prompt)
        
        # Финальная чистка текста от мусора
        final_clean_text = final_response.text.replace("```html", "").replace("```", "").strip()
        
        return {"reply": final_clean_text}
        
    except Exception as e:
        # Если что-то пошло не так, выводим ошибку (потом заменим на вежливую фразу)
        return {"reply": f"Системная ошибка: {str(e)}"}
