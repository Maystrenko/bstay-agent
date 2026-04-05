import os
import json
import urllib.parse
import time
import random
import requests
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
# Пытаемся импортировать новый SDK
try:
    from google import genai
    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

gemini_keys = [k.strip() for k in os.environ.get("GEMINI_API_KEY", "").split(",") if k.strip()]
groq_keys = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]

class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list
    lang: str = "en"

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    debug_info = []
    
    if not SDK_AVAILABLE:
        return JSONResponse(content={"reply": "Ошибка: Библиотека google-genai не установлена. Проверьте requirements.txt"})

    prompt = f"Extract city (English) and 2-sentence greeting ({payload.lang}) from: {payload.message}. Return JSON: {{\"city\": \"CityName\", \"text\": \"Greeting\"}}"
    ai_response = None
    engine = ""

    # 1. ТЕСТ GEMINI
    if gemini_keys:
        for g_key in gemini_keys:
            try:
                client = genai.Client(api_key=g_key)
                res = client.models.generate_content(model='gemini-2.0-flash-lite', contents=prompt)
                if res.text:
                    ai_response = res.text
                    engine = "Gemini 2.0"
                    break
            except Exception as e:
                debug_info.append(f"Gemini Error: {str(e)[:50]}")

    # 2. ТЕСТ GROQ (если Gemini не сработал)
    if not ai_response and groq_keys:
        for r_key in groq_keys:
            try:
                url = "https://api.groq.com/openai/v1/chat/completions"
                headers = {"Authorization": f"Bearer {r_key}", "Content-Type": "application/json"}
                data = {
                    "model": "llama-3.3-70b-specdec",
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"}
                }
                resp = requests.post(url, headers=headers, json=data, timeout=10)
                if resp.status_code == 200:
                    ai_response = resp.json()['choices'][0]['message']['content']
                    engine = "Groq"
                    break
                else:
                    debug_info.append(f"Groq Status: {resp.status_code}")
            except Exception as e:
                debug_info.append(f"Groq Error: {str(e)[:50]}")

    if not ai_response:
        # ВЫВОДИМ ВСЕ ОШИБКИ В ЧАТ
        error_report = " | ".join(debug_info)
        return JSONResponse(content={"reply": f"Ни один ИИ не ответил. Логи: {error_report}"})

    # ПАРСИНГ И ССЫЛКА (как в прошлом коде)
    try:
        data = json.loads(ai_response[ai_response.find('{'):ai_response.rfind('}')+1])
        city = data.get("city", "none")
        text = data.get("text", "Hello!")
        
        if city.lower() == "none": return JSONResponse(content={"reply": text})

        city_enc = urllib.parse.quote(city)
        link = f"https://www.stay22.com/allez/bstay24?campaign=ai_bot&address={city_enc}&link=https://www.booking.com/searchresults.html?ss={city_enc}%26lang={payload.lang}"
        
        return JSONResponse(content={"reply": f"{text}<br><br><a href='{link}' target='_blank' style='display:inline-block; padding:12px 24px; background:#003580; color:white; text-decoration:none; border-radius:6px;'>🏨 Отели в {city}</a><br><small>{engine}</small>"})
    except Exception as e:
        return JSONResponse(content={"reply": f"Ошибка парсинга: {str(e)} | Raw: {ai_response[:100]}"})
