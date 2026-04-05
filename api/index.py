import json
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import google.generativeai as genai

# Вот она, та самая строчка, которую потерял Vercel!
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 1. ВАШИ КЛЮЧИ 
# Вставьте ключ строго между кавычками!
# ==========================================
GEMINI_API_KEY = "AIzaSyA9A_2iWX83RstoFllyI_3K1FNJY6hoDhs" 
STAY22_AID = "bstay24"

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-pro')

class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    system_instructions = """
    Ты - умный ИИ-агент туристического сервиса bstay24. Твоя задача: понять, какой город ищет пользователь.
    Если пользователь называет локацию (город, страну), верни СТРОГО валидный JSON: {"status": "search", "city": "Название города на английском"}
    Если город не указан, пользователь здоровается или говорит на отвлеченные темы, верни JSON: {"status": "chat", "reply": "Твой ответ пользователю (в HTML)"}
    """
    
    prompt = f"{system_instructions}\nЗапрос пользователя: {payload.message}"
    
    try:
        response = model.generate_content(prompt)
        
        result_text = response.text
        start = result_text.find('{')
        end = result_text.rfind('}') + 1
        clean_json = result_text[start:end]
        data = json.loads(clean_json)
        
        if data.get("status") == "chat":
            return {"reply": data["reply"]}
            
        city = data.get("city")
        
        booking_search_url = f"https://www.booking.com/searchresults.html?ss={city}"
        stay22_link = f"https://www.stay22.com/allez/{STAY22_AID}?campaign=ai-bot&link={booking_search_url}"
        
        answer_prompt = f"""
        Пользователь ищет отели в: {city}.
        Напиши приветливый текст. Кратко посоветуй 2 отличных района для туристов.
        Заверши ответ этой HTML-кнопкой (не меняй её код):
        <br><br><a href='{stay22_link}' target='_blank' style='display:inline-block; padding:12px 24px; background:#007BFF; color:white; text-decoration:none; border-radius:8px; font-weight:bold; box-shadow: 0 4px 6px rgba(0,0,0,0.1);'>Посмотреть отели и цены в {city}</a>
        Верни ТОЛЬКО готовый HTML-код ответа.
        """
        
        final_response = model.generate_content(answer_prompt)
        return {"reply": final_response.text}
        
    except Exception as e:
        try:
            # Запрашиваем у Google список всех разрешенных нам моделей
            allowed_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
            return {"reply": f"Модель не найдена! Но Google говорит, что вам РАЗРЕШЕНЫ эти модели: {', '.join(allowed_models)}"}
        except Exception:
            return {"reply": f"Системная ошибка: {str(e)}"}
