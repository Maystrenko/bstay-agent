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

# Пытаемся импортировать новый SDK Gemini
try:
    from google import genai
    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# Ключи из Vercel
gemini_keys = [k.strip() for k in os.environ.get("GEMINI_API_KEY", "").split(",") if k.strip()]
groq_keys = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]

STAY22_AID = "bstay24"
LANG_MAP = {'ru': 'Russian', 'en': 'English', 'de': 'German', 'fr': 'French', 'es': 'Spanish'}

class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list
    lang: str = "en"

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    debug_info = []
    ai_response = None
    engine = ""
    target_lang = LANG_MAP.get(payload.lang, "English")

    # ЖЕСТКИЙ ПРОМПТ ДЛЯ JSON (Чтобы Groq не выдавал 400)
    prompt = f"""
    Return a JSON object with two fields:
    1. "city": the destination city from the user message in English.
    2. "text": a 2-sentence greeting in {target_lang} about this city.
    
    User message: "{payload.message}"
    
    JSON format: {{"city": "CityName", "text": "Greeting"}}
    """

    # 1. СНАЧАЛА ПРОБУЕМ GROQ (так как Gemini у нас в ауте 429)
    if groq_keys:
        for r_key in groq_keys:
            try:
                url = "https://api.groq.com/openai/v1/chat/completions"
                headers = {"Authorization": f"Bearer {r_key}", "Content-Type": "application/json"}
                data = {
                    "model": "llama-3.3-70b-versatile", # Самая стабильная модель
                    "messages": [{"role": "system", "content": "You are a travel assistant that always outputs JSON."},
                                 {"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"}
                }
                resp = requests.post(url, headers=headers, json=data, timeout=10)
                if resp.status_code == 200:
                    ai_response = resp.json()['choices'][0]['message']['content']
                    engine = "Groq (Llama 3.3)"
                    break
                else:
                    debug_info.append(f"Groq {resp.status_code}: {resp.text[:50]}")
            except Exception as e:
                debug_info.append(f"Groq Error: {str(e)[:40]}")

    # 2. РЕЗЕРВ: GEMINI (если Groq не сработал)
    if not ai_response and gemini_keys and SDK_AVAILABLE:
        random.shuffle(gemini_keys)
        for g_key in gemini_keys:
            try:
                client = genai.Client(api_key=g_key)
                res = client.models.generate_content(model='gemini-2.0-flash-lite', contents=prompt)
                if res.text:
                    ai_response = res.text
                    engine = "Gemini 2.0"
                    break
            except Exception as e:
                debug_info.append(f"Gemini Error: {str(e)[:40]}")

    # ЕСЛИ ВСЁ ЕЩЁ НЕТ ОТВЕТА
    if not ai_response:
        return JSONResponse(content={"reply": f"Все ИИ-ключи исчерпаны. Добавьте новые ключи в Vercel. Логи: {'; '.join(debug_info[:2])}"})

    # ПАРСИНГ И КНОПКА
    try:
        data = json.loads(ai_response[ai_response.find('{'):ai_response.rfind('}')+1])
        city = data.get("city", "none")
        text = data.get("text", "City found!")

        if city.lower() == "none" or len(city) < 3:
            return JSONResponse(content={"reply": text})

        city_enc = urllib.parse.quote(city)
        # Чистая партнерская ссылка Stay22
        link = f"https://www.stay22.com/allez/{STAY22_AID}?campaign=ai_bot&address={city_enc}&link=https://www.booking.com/searchresults.html?ss={city_enc}%26lang={payload.lang}"
        
        btn_text = {'ru': f"🏨 Отели в {city}", 'en': f"🏨 Hotels in {city}"}.get(payload.lang, f"Hotels in {city}")

        return JSONResponse(content={"reply": f"{text}<br><br><a href='{link}' target='_blank' style='display:inline-block; padding:12px 24px; background:#003580; color:white; text-decoration:none; border-radius:6px; font-weight:bold;'>{btn_text}</a><br><small style='font-size:9px; color:gray;'>Engine: {engine}</small>"})
    except Exception as e:
        return JSONResponse(content={"reply": f"Ошибка обработки: {str(e)}"})
