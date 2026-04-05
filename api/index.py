import os
import json
import urllib.parse
import time
import random
import requests # Для Groq используем простой requests
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

# ЧИТАЕМ КЛЮЧИ GEMINI И GROQ
gemini_keys = [k.strip() for k in os.environ.get("GEMINI_API_KEY", "").split(",") if k.strip()]
groq_keys = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]

STAY22_AID = "bstay24"
LANG_MAP = {'ru': 'Russian', 'en': 'English', 'de': 'German', 'fr': 'French', 'es': 'Spanish'}

class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list
    lang: str = "en"

def call_groq(prompt, keys):
    """Резервный метод через Groq (Llama 3)"""
    if not keys: return None
    key = random.choice(keys)
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    data = {
        "model": "llama-3.3-70b-specdec", # Мощная и быстрая модель 2026 года
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"}
    }
    try:
        resp = requests.post(url, headers=headers, json=data, timeout=10)
        return resp.json()['choices'][0]['message']['content']
    except:
        return None

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    try:
        current_time = str(int(time.time()))
        target_lang = LANG_MAP.get(payload.lang, "English")
        
        prompt = f"""
        Analyze: "{payload.message}"
        1. Extract destination city in English.
        2. Write 2-sentence friendly greeting in {target_lang} about this city.
        Return ONLY JSON: {{"city": "CityName", "text": "Greeting text"}}
        """
        
        ai_response = None
        engine = ""

        # ПЫТАЕМСЯ GEMINI
        if gemini_keys:
            random.shuffle(gemini_keys) # Перемешиваем ключи
            for g_key in gemini_keys:
                try:
                    genai.configure(api_key=g_key)
                    # Используем ТОЛЬКО ту модель, которая у тебя не выдала 404
                    model = genai.GenerativeModel('gemini-2.0-flash-lite')
                    res = model.generate_content(prompt)
                    if res.text:
                        ai_response = res.text
                        engine = "Gemini"
                        break
                except Exception as e:
                    continue # Пробуем следующий ключ Gemini

        # ЕСЛИ GEMINI НЕ СРАБОТАЛ (ЛИМИТЫ) -> ИДЕМ В GROQ
        if not ai_response and groq_keys:
            ai_response = call_groq(prompt, groq_keys)
            engine = "Groq (Llama 3)"

        if not ai_response:
            return JSONResponse(content={"reply": "Все ИИ-сервисы временно перегружены. Попробуйте через 30 секунд."})

        # ПАРСИНГ
        clean_json = ai_response[ai_response.find('{'):ai_response.rfind('}')+1]
        data = json.loads(clean_json)
        
        city = data.get("city", "none")
        text = data.get("text", "Hello!")

        if city.lower() == "none" or len(city) < 3:
            return JSONResponse(content={"reply": text})

        # ССЫЛКА
        city_enc = urllib.parse.quote(city)
        stay22_link = f"https://www.stay22.com/allez/{STAY22_AID}?campaign=ai_bot&address={city_enc}&link=https://www.booking.com/searchresults.html?ss={city_enc}%26lang={payload.lang}"
        
        btn_text = {'ru': f"🏨 Отели в {city}", 'en': f"🏨 Hotels in {city}"}.get(payload.lang, f"Hotels in {city}")

        button_html = f"""
        <br><br>
        <a href='{stay22_link}' target='_blank' style='display:inline-block; padding:14px 28px; background:#003580; color:white; text-decoration:none; border-radius:6px; font-weight:bold;'>
           {btn_text}
        </a>
        <br><small style='color:gray; font-size:9px;'>Engine: {engine} | City: {city}</small>
        """
        
        return JSONResponse(content={"reply": text + button_html})
        
    except Exception as e:
        return JSONResponse(content={"reply": f"Ошибка: {str(e)}"})
