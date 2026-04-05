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
# Используем новый SDK 2026 года
from google import genai 

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# ЧИТАЕМ КЛЮЧИ
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
    if not keys: return None
    key = random.choice(keys)
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    data = {
        "model": "llama-3.3-70b-specdec",
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"}
    }
    try:
        resp = requests.post(url, headers=headers, json=data, timeout=10)
        return resp.json()['choices'][0]['message']['content']
    except: return None

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    try:
        current_time = str(int(time.time()))
        target_lang = LANG_MAP.get(payload.lang, "English")
        
        prompt = f"""
        User message: "{payload.message}"
        1. Extract the destination city in English.
        2. Write a 2-sentence friendly greeting in {target_lang} about this city.
        Return ONLY JSON: {{"city": "CityName", "text": "Greeting text"}}
        """
        
        ai_response = None
        engine = ""

        # 1. ПРОБУЕМ GEMINI (Новый SDK)
        if gemini_keys:
            random.shuffle(gemini_keys)
            for g_key in gemini_keys:
                try:
                    # Новая инициализация клиента в 2026
                    client = genai.Client(api_key=g_key)
                    # Модель flash-lite самая экономная по лимитам
                    res = client.models.generate_content(
                        model='gemini-2.0-flash-lite', 
                        contents=prompt
                    )
                    if res.text:
                        ai_response = res.text
                        engine = "Gemini 2.0"
                        break
                except: continue

        # 2. РЕЗЕРВ (GROQ)
        if not ai_response and groq_keys:
            ai_response = call_groq(prompt, groq_keys)
            engine = "Groq (Llama 3)"

        if not ai_response:
            return JSONResponse(content={"reply": "AI services are busy. Please retry in 10s."})

        # ПАРСИНГ JSON
        clean_json = ai_response[ai_response.find('{'):ai_response.rfind('}')+1]
        data = json.loads(clean_json)
        city = data.get("city", "none")
        text = data.get("text", "Hello!")

        if city.lower() == "none" or len(city) < 3:
            return JSONResponse(content={"reply": text})

        # ССЫЛКА STAY22
        city_enc = urllib.parse.quote(city)
        stay22_link = f"https://www.stay22.com/allez/{STAY22_AID}?campaign=ai_bot&address={city_enc}&link=https://www.booking.com/searchresults.html?ss={city_enc}%26lang={payload.lang}"
        
        btn_text = {'ru': f"🏨 Отели в {city}", 'en': f"🏨 Hotels in {city}"}.get(payload.lang, f"Hotels in {city}")

        button_html = f"""
        <br><br>
        <a href='{stay22_link}' target='_blank' style='display:inline-block; padding:14px 28px; background:#003580; color:white; text-decoration:none; border-radius:6px; font-weight:bold;'>
           {btn_text}
        </a>
        <br><small style='color:gray; font-size:9px;'>Engine: {engine} | bstay24</small>
        """
        
        return JSONResponse(content={"reply": text + button_html})
        
    except Exception as e:
        return JSONResponse(content={"reply": f"Система обновляется, попробуйте еще раз. ({str(e)})"})
