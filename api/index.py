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
    try:
        # --- ТОЧКА ПОЛОМКИ №1: Извлечение города ---
        extract_prompt = f"Identify the city in: '{payload.message}'. Reply ONLY with the city name in English. No dots. If no city, reply 'none'."
        response = model.generate_content(extract_prompt)
        
        # Очистка
        raw_city = response.text.strip().replace(".", "").replace("'", "")
        # Берем только первое слово (защита от лишней болтовни ИИ)
        detected_city = raw_city.split()[0] if raw_city else "none"
        
        if "none" in detected_city.lower() or len(detected_city) < 3:
            return JSONResponse(content={"reply": "Я не совсем понял город. Напишите, например: 'Хочу в Париж'"})

        # --- ТОЧКА ПОЛОМКИ №2: Формирование ссылки ---
        # Мы создаем чистую ссылку на Букинг
        booking_url = f"https://www.booking.com/searchresults.html?ss={detected_city}"
        
        # Кодируем её для Stay22 (чтобы спецсимволы не ломали переход)
        encoded_url = urllib.parse.quote(booking_url, safe='')
        
        # Финальная партнерская ссылка
        stay22_link = f"https://www.stay22.com/allez/{STAY22_AID}?campaign=ai-bot&link={encoded_url}"
        
        # --- ТОЧКА ПОЛОМКИ №3: Ответ пользователю ---
        answer_prompt = f"Write 2 short sentences in Russian about traveling to {detected_city}. Mention 1 top landmark."
        final_res = model.generate_content(answer_prompt)
        ai_text = final_res.text.replace("```html", "").replace("```", "").strip()

        # СОБИРАЕМ ОТВЕТ С ДИАГНОСТИКОЙ
        debug_info = f"""
        <hr style='border:1px dashed #ccc; margin: 20px 0;'>
        <div style='font-size:10px; color:gray; line-height:1.2;'>
            <b>DEBUG LOG:</b><br>
            Ввод: {payload.message}<br>
            ИИ извлек город: <span style='color:red;'>{detected_city}</span><br>
            Финальная ссылка: <a href='{stay22_link}' target='_blank' style='color:blue; word-break:break-all;'>{stay22_link[:60]}...</a>
        </div>
        """
        
        button_html = f"<br><br><a href='{stay22_link}' target='_blank' style='display:inline-block; padding:12px 24px; background:#007BFF; color:white; text-decoration:none; border-radius:8px; font-weight:bold;'>Посмотреть отели в {detected_city}</a>"
        
        return JSONResponse(
            content={"reply": ai_text + button_html + debug_info},
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"}
        )
        
    except Exception as e:
        return {"reply": f"Ошибка в коде: {str(e)}"}
