import os
import json
import urllib.parse
import random
import requests
import re
from datetime import datetime, timedelta
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

groq_keys = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")
RAPID_HOST = "booking-com18.p.rapidapi.com"
STAY22_AID = "bstay24"

class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list
    lang: str = "en"

def clean_json_string(text):
    """Очистка строки от Markdown и лишнего мусора перед парсингом JSON"""
    text = re.sub(r'```json\s*|\s*```', '', text) # Убираем блоки кода
    text = text.strip()
    return text

def get_hotels_data(city_name):
    try:
        headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": RAPID_HOST}
        l_res = requests.get(f"https://{RAPID_HOST}/stays/auto-complete", headers=headers, params={"query": city_name}, timeout=7)
        l_data = l_res.json()
        l_list = l_data if isinstance(l_data, list) else l_data.get('data', [])
        if not l_list: return None
        
        dest_id = l_list[0].get('id')
        in_d = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
        out_d = (datetime.now() + timedelta(days=33)).strftime('%Y-%m-%d')
        
        params = {"locationId": dest_id, "checkinDate": in_d, "checkoutDate": out_d, "adults": "2", "rooms": "1", "currency_code": "USD"}
        h_res = requests.get(f"https://{RAPID_HOST}/stays/search", headers=headers, params=params, timeout=12)
        h_data = h_res.json()
        
        raw = h_data if isinstance(h_data, list) else h_data.get('data', [])
        if not isinstance(raw, list): raw = h_data.get('data', {}).get('hotels', []) or h_data.get('data', {}).get('results', [])
        
        return [{"id": str(h.get('hotel_id') or h.get('id')), "name": h.get('name') or h.get('hotel_name')} for h in raw if (h.get('id') or h.get('hotel_id'))][:10]
    except Exception as e:
        print(f"HOTEL API ERROR: {e}")
        return None

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    try:
        if not groq_keys: return JSONResponse(content={"reply": "Ошибка: Нет ключей Groq."})
        g_key = random.choice(groq_keys)
        headers = {"Authorization": f"Bearer {g_key}"}

        # 1. Извлекаем город
        c_payload = {"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": f"Extract city in English from: '{payload.message}'. JSON: {{\"city\": \"Name\"}}"}], "response_format": {"type": "json_object"}}
        c_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=c_payload, timeout=10)
        c_text = clean_json_string(c_res.json()['choices'][0]['message']['content'])
        city = json.loads(c_text).get("city", "none")

        if city.lower() == "none":
            return JSONResponse(content={"reply": "Пожалуйста, напишите название города."})

        # 2. Отели
        hotels = get_hotels_data(city)
        if not hotels:
            return JSONResponse(content={"reply": f"Не удалось найти отели в {city}. Попробуйте позже."})

        # 3. Генерируем гид
        g_prompt = f"Create a travel guide for {city} in Russian based on: {json.dumps(hotels)}. Format as JSON with keys: intro (string), categories (list of objects with name, hotels[name, id, desc]), tips (string)."
        g_payload = {"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": g_prompt}], "response_format": {"type": "json_object"}}
        
        g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=g_payload, timeout=25)
        g_text = clean_json_string(g_res.json()['choices'][0]['message']['content'])
        g_json = json.loads(g_text)

        # 4. Верстка
        html = f"<div style='font-family: sans-serif;'><p>{g_json.get('intro','')}</p>"
        for cat in g_json.get('categories', []):
            html += f"<h4 style='color:#003580; border-bottom: 1px solid #eee; padding-bottom: 5px;'>{cat['name']}</h4>"
            for h in cat.get('hotels', []):
                link = f"https://www.stay22.com/allez/booking/{h['id']}?aid={STAY22_AID}"
                html += f"""
                <div style='margin-bottom:12px; padding:10px; background:#f9f9f9; border-radius:8px; border:1px solid #eee;'>
                    <div style='display:flex; justify-content:space-between; align-items:center;'>
                        <span style='font-size:14px;'><b>{h['name']}</b></span>
                        <a href='{link}' target='_blank' style='background:#007BFF; color:#fff; text-decoration:none; padding:5px 12px; border-radius:6px; font-size:12px; font-weight:bold;'>Book</a>
                    </div>
                    <p style='font-size:12px; color:#666; margin:5px 0 0;'>{h['desc']}</p>
                </div>"""
        html += f"<p style='background:#e9f7ef; padding:10px; border-radius:8px; font-size:12px;'><b>Совет:</b> {g_json.get('tips','')}</p></div>"
        
        return JSONResponse(content={"reply": html})

    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        return JSONResponse(content={"reply": f"Ошибка обработки: {str(e)[:100]}"})
