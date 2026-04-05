import os
import json
import urllib.parse
import time
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
gemini_keys = [k.strip() for k in os.environ.get("GEMINI_API_KEY", "").split(",") if k.strip()]
groq_keys = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")

RAPID_HOST = "booking-com18.p.rapidapi.com"
STAY22_AID = "bstay24"
LANG_MAP = {'ru': 'Russian', 'en': 'English', 'de': 'German', 'fr': 'French', 'es': 'Spanish'}

class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list
    lang: str = "en"

def get_hotels_data(city_name, lang='ru'):
    """Получение 10 отелей с их ID для точных ссылок"""
    if not RAPID_API_KEY: return None, "No API Key"
    headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": RAPID_HOST}
    try:
        # 1. Поиск локации
        loc_res = requests.get(f"https://{RAPID_HOST}/stays/auto-complete", headers=headers, params={"query": city_name}, timeout=6)
        loc_list = loc_res.json() if isinstance(loc_res.json(), list) else loc_res.json().get('data', [])
        if not loc_list: return None, "City not found"
        dest_id = loc_list[0].get('id')

        # 2. Поиск отелей
        in_d = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
        out_d = (datetime.now() + timedelta(days=33)).strftime('%Y-%m-%d')
        params = {"locationId": dest_id, "checkinDate": in_d, "checkoutDate": out_d, "adults": "2", "rooms": "1", "currency_code": "USD"}
        
        res = requests.get(f"https://{RAPID_HOST}/stays/search", headers=headers, params=params, timeout=12)
        data = res.json()
        
        if isinstance(data, list): hotels = data
        else:
            d_block = data.get('data', {})
            hotels = d_block if isinstance(d_block, list) else (d_block.get('hotels', []) or d_block.get('results', []))
        
        return hotels[:10], None
    except Exception as e:
        return None, str(e)

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    try:
        t_lang = LANG_MAP.get(payload.lang, "Russian")
        
        # 1. Извлекаем город
        city_prompt = f"Extract only city name in English from: '{payload.message}'. Return JSON: {{\"city\": \"CityName\"}}"
        g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", 
            headers={"Authorization": f"Bearer {random.choice(groq_keys)}"}, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": city_prompt}], "response_format": {"type": "json_object"}}, timeout=10).json()
        city_name = json.loads(g_res['choices'][0]['message']['content']).get("city", "none")

        if city_name.lower() == "none":
            return JSONResponse(content={"reply": "Пожалуйста, напишите название города, чтобы я подготовил гид."})

        # 2. Получаем отели из API
        hotels, err = get_hotels_safe = get_hotels_data(city_name, payload.lang)
        if not hotels:
            return JSONResponse(content={"reply": f"Не удалось найти отели в {city_name}. Попробуйте другой город."})

        # Создаем словарь для ИИ, чтобы он мог сопоставить ID и Название
        hotels_map = []
        for h in hotels:
            # Важно: достаем hotel_id для прямой ссылки
            h_id = h.get('hotel_id') or h.get('id')
            h_name = h.get('name') or h.get('hotel_name', 'Hotel')
            hotels_map.append({"id": h_id, "name": h_name})

        # 3. Генерируем расширенный ответ через ИИ
        guide_prompt = f"""
        Create a detailed travel guide for {city_name} in {t_lang}.
        Categorize these hotels into 'Luxury', 'Modern/Boutique', and 'Budget': {json.dumps([h['name'] for h in hotels_map])}.
        For each category, list 2-3 hotels with 1-2 sentences of description.
        Add 'Travel Tips' at the end.
        Return ONLY JSON: 
        {{
          "intro": "Intro text",
          "categories": [
            {{ "name": "Category", "hotels": [ {{ "name": "Hotel Name", "desc": "Short description" }} ] }}
          ],
          "tips": "Tips text"
        }}
        """
        
        guide_res = requests.post("https://api.groq.com/openai/v1/chat/completions", 
            headers={"Authorization": f"Bearer {random.choice(groq_keys)}"}, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": guide_prompt}], "response_format": {"type": "json_object"}}, timeout=15).json()
        
        g_data = json.loads(guide_res['choices'][0]['message']['content'])

        # 4. Собираем HTML с ТОЧНЫМИ ссылками
        html = f"<div style='line-height: 1.6; color: #333;'>"
        html += f"<p>{g_data.get('intro')}</p>"

        # Создаем быстрый поиск ID по имени для сборки ссылок
        id_lookup = {h['name']: h['id'] for h in hotels_map}

        for cat in g_data.get('categories', []):
            html += f"<h3 style='color: #003580; margin-top: 20px; border-bottom: 1px solid #eee;'>{cat['name']}</h3>"
            for h_info in cat.get('hotels', []):
                name = h_info['name']
                desc = h_info['desc']
                h_id = id_lookup.get(name)

                # ФОРМИРУЕМ ТОЧНУЮ ССЫЛКУ
                if h_id:
                    # Прямая ссылка на конкретный отель на Booking
                    booking_url = f"https://www.booking.com/hotel/id/{h_id}.html"
                    # Оборачиваем в Stay22 Allez
                    final_link = f"https://www.stay22.com/allez/{STAY22_AID}?link={urllib.parse.quote(booking_url)}&campaign=exact_match"
                else:
                    # Запасной вариант: поиск по названию
                    final_link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(name + ' ' + city_name)}"

                html += f"""
                <div style='margin-bottom: 15px; padding: 12px; background: #fcfcfc; border-radius: 10px; border-left: 4px solid #007BFF;'>
                    <div style='display: flex; justify-content: space-between; align-items: center;'>
                        <strong style='font-size: 15px;'>{name}</strong>
                        <a href='{final_link}' target='_blank' style='background: #007BFF; color: white; text-decoration: none; padding: 5px 15px; border-radius: 6px; font-weight: bold; font-size: 12px;'>Book Now</a>
                    </div>
                    <p style='font-size: 13px; color: #555; margin: 5px 0 0 0;'>{desc}</p>
                </div>"""

        html += f"<div style='background: #e9f7ef; padding: 15px; border-radius: 10px; margin-top: 20px;'><strong>💡 Советы:</strong><br><small>{g_data.get('tips')}</small></div>"
        
        # Общая кнопка
        city_enc = urllib.parse.quote(city_name)
        all_url = f"https://www.stay22.com/allez/{STAY22_AID}?address={city_enc}"
        html += f"<br><a href='{all_url}' target='_blank' style='display: block; text-align: center; padding: 15px; background: #003580; color: white; text-decoration: none; border-radius: 10px; font-weight: bold;'>Смотреть все отели в {city_name}</a></div>"

        return JSONResponse(content={"reply": html})

    except Exception as e:
        return JSONResponse(content={"reply": f"Ошибка: {str(e)}"})
