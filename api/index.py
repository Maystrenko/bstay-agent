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

# Приоритет на модели с лимитом 1500 запросов
MODELS_TO_TRY = ["gemini-1.5-flash", "gemini-1.5-flash-8b", "gemini-2.0-flash-lite", "gemini-flash-latest"]

class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    try:
        current_time = str(int(time.time()))
        
        # ЭКОНОМИЯ КВОТЫ: Просим всё за ОДИН запрос
        prompt = f"""
        Analyze this user message: "{payload.message}"
        1. Extract the destination city in English.
        2. Write a 2-sentence friendly greeting in Russian about this city.
        Return ONLY a JSON object: {{"city": "CityName", "text": "Russian text"}}. 
        If no city found, set city to "none".
        """
        
        # Перебор моделей для надежности
        ai_response = ""
        used_model = ""
        for m_name in MODELS_TO_TRY:
            try:
                model = genai.GenerativeModel(m_name)
                res = model.generate_content(prompt)
                ai_response = res.text
                used_model = m_name
                break
            except Exception as e:
                if "404" in str(e): continue
                else: raise e

        # Чистим JSON от мусора
        clean_json = ai_response.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_json[clean_json.find('{'):clean_json.rfind('}')+1])
        
        detected_city = data.get("city", "none")
        ai_text = data.get("text", "Привет! Назовите город?")

        if detected_city.lower() == "none" or len(detected_city) < 3:
            return JSONResponse(content={"reply": ai_text})

        # Ссылка с защитой от Манчестера
        city_encoded = urllib.parse.quote(detected_city)
        booking_url = f"https://www.booking.com/searchresults.html?ss={city_encoded}&lang=ru"
        
        params = {
            "campaign": "ai_bot",
            "link": booking_url,
            "address": detected_city, # ПРИНУДИТЕЛЬНО ПЕРЕБИВАЕМ МАНЧЕСТЕР
            "t": current_time
        }
        stay22_link = f"https://www.stay22.com/allez/{STAY22_AID}?{urllib.parse.urlencode(params)}"
        
        button_html = f"""
        <br><br>
        <a href='{stay22_link}' target='_blank' style='display:inline-block; padding:14px 28px; background:#003580; color:white; text-decoration:none; border-radius:4px; font-weight:bold;'>
           🏨 Отели в {detected_city}
        </a>
        <br><small style='color:gray; font-size:9px;'>ID: {current_time[-4:]} | Model: {used_model}</small>
        """
        
        return JSONResponse(
            content={"reply": ai_text + button_html},
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"}
        )
        
    except Exception as e:
        if "429" in str(e):
            return JSONResponse(content={"reply": "Google Free Tier лимит. Подождите 30 сек."})
        return JSONResponse(content={"reply": f"Ошибка: {str(e)}"})
