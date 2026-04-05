from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()

# КРИТИЧЕСКИ ВАЖНО: Разрешаем запросы (CORS). 
# Без этого браузер заблокирует запросы с bstay24.com к Vercel.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Пока разрешаем запросы отовсюду для тестов
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Формат данных, который мы ждем от вашего сайта
class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    user_msg = payload.message
    
    # ----------------------------------------------------
    # Здесь скоро будут:
    # 1. Запрос к Gemini (понять город и даты)
    # 2. Запрос к RapidAPI (найти отели и цены)
    # 3. Подстановка партнерских ссылок Stay22
    # ----------------------------------------------------
    
    # А пока возвращаем тестовый ответ, чтобы проверить связь:
    test_reply = f"✅ Соединение установлено! Я успешно получил ваше сообщение: <b>«{user_msg}»</b>. Бэкенд на Python работает!"
    
    return {"reply": test_reply}
