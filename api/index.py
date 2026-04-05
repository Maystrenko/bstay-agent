import json
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import google.generativeai as genai

# ==========================================
# 1. ВАШИ КЛЮЧИ (ОБЯЗАТЕЛЬНО ЗАПОЛНИТЕ!)
# ==========================================
GEMINI_API_KEY = "ВСТАВЬТЕ_СЮДА_ВАШ_КЛЮЧ_GEMINI" 
STAY22_AID = "bstay24" # Ваш ID в партнерской программе Stay22

# Настройка нейросети
genai.configure(api_key=GEMINI_API_KEY)
# Используем самую быструю и современную модель
model = genai.GenerativeModel('gemini-1.5-flash')

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    # ШАГ 1: ИИ анализирует текст и достает город
    system_instructions = """
    Ты - умный ИИ-агент туристического сервиса bstay24. Твоя задача: понять, какой город ищет пользователь.
    Если пользователь называет локацию (город, страну), верни СТРОГО валидный JSON: {"status": "search", "city": "Название города на английском"}
    Если город не указан, пользователь здоровается или говорит на отвлеченные темы, верни JSON: {"status": "chat", "reply": "Твой ответ пользователю (в HTML)"}
    """
    
    prompt = f"{system_instructions}\nЗапрос пользователя: {payload.message}"
    
    try:
        # Получаем данные от Gemini
        response = model.generate_content(prompt)
        
        # Надежно извлекаем JSON из ответа ИИ
        result_text = response.text
        start = result_text.find('{')
        end = result_text.rfind('}') + 1
        clean_json = result_text[start:end]
        data = json.loads(clean_json)
        
        # Если это просто разговор (города нет)
        if data.get("status") == "chat":
            return {"reply": data["reply"]}
            
        city = data.get("city")
        
        # ШАГ 2: Генерация вашей партнерской ссылки Stay22
        booking_search_url = f"https://www.booking.com/searchresults.html?ss={city}"
        stay22_link = f"https://www.stay22.com/allez/{STAY22_AID}?campaign=ai-bot&link={booking_search_url}"
        
        # ШАГ 3: ИИ формирует красивый ответ с рекомендациями и кнопкой
        answer_prompt = f"""
        Пользователь ищет отели в: {city}.
        Напиши приветливый текст на языке пользователя. 
        Кратко назови 2 отличных района или популярных отеля в этом городе из своих знаний.
        ОБЯЗАТЕЛЬНО заверши свой ответ этой HTML-кнопкой для бронирования:
        <br><br><a href='{stay22_link}' target='_blank' style='display:inline-block; padding:12px 24px; background:#007BFF; color:white; text-decoration:none; border-radius:8px; font-weight:bold; box-shadow: 0 4px 6px rgba(0,0,0,0.1);'>Посмотреть отели и цены в {city}</a>
        Верни ТОЛЬКО готовый HTML-код ответа, без лишних слов.
        """
        
        final_response = model.generate_content(answer_prompt)
        return {"reply": final_response.text}
        
    except Exception as e:
        return {"reply": "Извините, я сейчас обрабатываю слишком много запросов! Пожалуйста, уточните ваш запрос."}
