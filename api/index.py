import os
import json
import urllib.parse
import random
import requests
from datetime import datetime, timedelta
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# Ключи и настройки
groq_keys = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")
RAPID_HOST = "booking-com18.p.rapidapi.com"
STAY22_AID = "bstay24"

class ChatPayload(BaseModel):
    message: str
    lang: str = "ru"

def get_hotels(city):
    """Поиск отелей: берем чуть больше, чтобы ИИ было из чего выбрать"""
    try:
        headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": RAPID_HOST}
        l_res = requests.get(f"https://{RAPID_HOST}/stays/auto-complete", headers=headers, params={"query": city}, timeout=6)
        dest_id = l_res.json()['data'][0]['id']
        
        params = {
            "locationId": dest_id, 
            "checkinDate": (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d'),
            "checkoutDate": (datetime.now() + timedelta(days=33)).strftime('%Y-%m-%d'),
            "adults": "2", "currency_code": "USD"
        }
        h_res = requests.get(f"https://{RAPID_HOST}/stays/search", headers=headers, params=params, timeout=10)
        data = h_res.json().get('data', [])
        if not isinstance(data, list): data = data.get('hotels', []) or data.get('results', [])
        return [{"id": str(h.get('hotel_id') or h.get('id')), "name": h.get('name') or h.get('hotel_name')} for h in data if h.get('id') or h.get('hotel_id')][:10]
    except: return None

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    try:
        g_key = random.choice(groq_keys)
        # 1. Извлекаем город
        c_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {g_key}"}, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": f"Extract city in English from: '{payload.message}'. JSON: {{\"c\": \"Name\"}}"}], "response_format": {"type": "json_object"}}, timeout=5)
        city = json.loads(c_res.json()['choices'][0]['message']['content']).get("c", "none")

        if city == "none": return JSONResponse(content={"reply": "Уточните название города, пожалуйста."})

        # 2. Получаем отели
        hotels = get_hotels(city)
        if not hotels: return JSONResponse(content={"reply": f"К сожалению, не удалось найти отели в {city}."})

        # 3. ГИД: СТРОГО ПО ОДНОМУ ОТЕЛЮ НА РУБРИКУ
        g_prompt = f"""
        Создай гид по отелям {city} на русском языке. 
        Используй этот список: {json.dumps(hotels)}. 
        Выбери ровно ТРИ отеля и распредели их по одному в каждую категорию:
        1. '💎 Премиум выбор' (самый роскошный)
        2. '🎨 Стильный бутик' (дизайнерский или необычный)
        3. '💰 Оптимальная цена' (лучший по соотношению цена/качество)
        
        Верни JSON: {{"i": "вступление", "cats": [ {{"n": "категория", "h": {{"id": "id", "n": "имя", "d": "описание"}} }} ], "t": "совет"}}
        """
        
        g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {g_key}"}, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": g_prompt}], "response_format": {"type": "json_object"}}, timeout=12)
        g = json.loads(g_res.json()['choices'][0]['message']['content'])

        # ФОРМИРУЕМ HTML
        html = f"<div style='font-family: Karla, sans-serif;'>"
        html += f"<p style='margin-bottom: 20px; color: #444;'>{g['i']}</p>"

        for cat in g['cats']:
            h = cat['h']
            link = f"https://www.stay22.com/allez/booking/{h['id']}?aid={STAY22_AID}"
            html += f"""
            <div style='margin-top: 20px;'>
                <span style='background: #003580; color: #fff; padding: 4px 12px; border-radius: 20px; font-size: 11px; font-weight: bold; text-transform: uppercase; letter-spacing: 0.5px;'>{cat['n']}</span>
                <div style='margin-top: 10px; padding: 15px; background: #ffffff; border-radius: 12px; border: 1px solid #eee; box-shadow: 0 4px 12px rgba(0,0,0,0.03);'>
                    <div style='display: flex; justify-content: space-between; align-items: center; gap: 10px;'>
                        <span style='font-weight: bold; font-size: 15px; color: #333;'>{h['n']}</span>
                        <a href='{link}' target='_blank' style='background: #007BFF; color: #fff; text-decoration: none; padding: 8px 18px; border-radius: 8px; font-weight: bold; font-size: 13px;'>Book</a>
                    </div>
                    <p style='font-size: 13px; color: #666; margin: 10px 0 0 0; line-height: 1.5;'>{h['d']}</p>
                </div>
            </div>"""

        html += f"<div style='background: #eef5ff; border-left: 4px solid #007BFF; padding: 15px; border-radius: 8px; margin-top: 25px; font-size: 13px;'><b>💡 Лайфхак:</b> {g['t']}</div>"
        
        # Финальная кнопка
        city_enc = urllib.parse.quote(city)
        html += f"<br><a href='https://www.stay22.com/allez/{STAY22_AID}?address={city_enc}' target='_blank' style='display: block; text-align: center; padding: 16px; background: #003580; color: white; text-decoration: none; border-radius: 10px; font-weight: bold;'>Посмотреть всё в {city} →</a></div>"

        return JSONResponse(content={"reply": html})
    except Exception as e:
        return JSONResponse(content={"reply": "Не удалось собрать подборку. Пожалуйста, попробуйте еще раз."})
