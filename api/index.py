import os
import json
import urllib.parse
import time
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

# Ставим 1.5-flash-8b на первое место - у неё лимит 1500 запросов в день!
MODELS_TO_TRY = ["gemini-1.5-flash-8b", "gemini-1.5-flash", "gemini-flash-latest"]

class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    try:
        # Если ключа нет, сразу выходим
        if not GEMINI_API_KEY:
            return JSONResponse(content={"reply": "Ошибка: Не настроен API KEY в Vercel"})

        current_time = str(int(time.time()))
        
        # 1 запрос для всего
        prompt = f"""
        User said: "{payload.message}"
        1. Extract destination city in English.
        2. Write 2-sentence friendly greeting in Russian about this city.
        Return ONLY JSON: {{"city": "CityName", "text": "Russian text"}}
        """
        
        ai_response = ""
        used_model = ""
        
        # Пытаемся получить ответ
        for m_name in MODELS_TO_TRY:
            try:
                model = genai.GenerativeModel(m_name)
                res = model.generate_content(prompt)
                if res and res.text:
                    ai_response = res.text
                    used_model = m_name
                    break
            except Exception as e:
                if "429" in str(e):
                    return JSONResponse(content={"reply": "Google лимит (429). Подождите 1-2 минуты или смените API ключ в Vercel."})
                continue

        if not ai_response:
            return JSONResponse(content={"reply": "ИИ временно недоступен. Попробуйте через минуту."})

        # Парсим JSON
        try:
            clean_json = ai_response.replace("```json", "").replace("```", "").strip()
            data = json.loads(clean_json[clean_json.find('{'):clean_json.rfind('}')+1])
        except:
            # Если ИИ выдал не JSON, пробуем спасти ситуацию
            return JSONResponse(content={"reply": "Не удалось распознать город. Напишите еще раз, например: 'Лондон'"})
        
        detected_city = data.get("city", "none")
        ai_text = data.get("text", "Я нашел отличные варианты!")

        if detected_city.lower() == "none":
            return JSONResponse(content={"reply": "В какой город вы хотите поехать?"})

        # Формируем ссылку (address= поможет избежать Манчестера)
        booking_url = f"https://www.booking.com/searchresults.html?ss={urllib.parse.quote(detected_city)}&lang=ru"
        params = {
            "campaign": "ai_search",
            "link": booking_url,
            "address": detected_city,
            "t": current_time
        }
        stay22_link = f"https://www.stay22.com/allez/{STAY22_AID}?{urllib.parse.urlencode(params)}"
        
        button_html = f"""
        <br><br>
        <a href='{stay22_link}' target='_blank' style='display:inline-block; padding:14px 28px; background:#003580; color:white; text-decoration:none; border-radius:4px; font-weight:bold;'>
           🏨 Отели в {detected_city}
        </a>
        <br><small style='color:gray; font-size:9px;'>Model: {used_model}</small>
        """
        
        return JSONResponse(content={"reply": ai_text + button_html})
        
    except Exception as e:
        return JSONResponse(content={"reply": f"Ошибка: {str(e)}"})
