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

# Ключи и настройки (Vercel)
gemini_keys = [k.strip() for k in os.environ.get("GEMINI_API_KEY", "").split(",") if k.strip()]
groq_keys = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")

RAPID_HOST = "booking-com18.p.rapidapi.com"
STAY22_AID = "bstay24" # Твой партнерский AID
LANG_MAP = {'ru': 'Russian', 'en': 'English', 'de': 'German', 'fr': 'French', 'es': 'Spanish'}

class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list
    lang: str = "en"

def get_hotels_data(city_name, lang='ru'):
    """Получение отелей с их реальными ID из Booking для прямых ссылок"""
    if not RAPID_API_KEY: return None, "No API Key"
    headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": RAPID_HOST}
    try:
        # 1. Поиск ID локации
        loc_res = requests.get(f"https://{RAPID_HOST}/stays/auto-complete", headers=headers, params={"query": city_name}, timeout=6)
        loc_json = loc_res.json()
        loc_list = loc_json if isinstance(loc_json, list) else loc_json.get('data', [])
        if not loc_list: return None, "City not found"
        dest_id = loc_list[0].get('id')

        # 2. Поиск 10 отелей
        in_d = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
        out_d = (datetime.now() + timedelta(days=33)).strftime('%Y-%m-%d')
        params = {"locationId": dest_id, "checkinDate": in_d, "checkoutDate": out_d, "adults": "2", "rooms": "1", "currency_code": "USD"}
        
        res = requests.get(f"https://{RAPID_HOST}/stays/search", headers=headers, params=params, timeout=12)
        data = res.json()
        
        hotels_raw = []
        if isinstance(data, list): 
            hotels_raw = data
        else:
            d_block = data.get('data', {})
            hotels_raw = d_block if isinstance(d_block, list) else (d_block.get('hotels', []) or d_block.get('results', []))
        
        # Собираем данные: Название + ID
        refined_hotels = []
        for h in hotels_raw:
            h_id = h.get('hotel_id') or h.get('id') or h.get('hotel_id')
            h_name = h.get('name') or h.get('hotel_name', 'Hotel')
            if h_id:
                refined_hotels.append({"id": str(h_id), "name": h_name})
        
        return refined_hotels[:10], None
    except Exception as e:
        return None, str(e)

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    try:
        t_lang = LANG_MAP.get(payload.lang, "Russian")
        
        # 1. Извлекаем город
        city_prompt = f"Extract ONLY city name in English from: '{payload.message}'. Return JSON: {{\"city\": \"CityName\"}}"
        g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", 
            headers={"Authorization": f"Bearer {random.choice(groq_keys)}"}, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": city_prompt}], "response_format": {"type": "json_object"}}, timeout=10).json()
        city_name = json.loads(g_res['choices'][0]['message']['content']).get("city", "none")

        if city_name.lower() == "none":
            return JSONResponse(content={"reply": "Пожалуйста, укажите город, чтобы я подготовил расширенный гид."})

        # 2. Берем реальные отели и их ID из API
        hotels, err = get_hotels_data(city_name, payload.lang)
        if not hotels:
            return JSONResponse(content={"reply": f"Не удалось найти актуальные предложения в {city_name}."})

        # 3. Генерируем расширенный гид через ИИ
        guide_prompt = f"""
        Based on these real hotels in {city_name}: {json.dumps(hotels)}.
        Create a detailed travel guide in {t_lang}.
        Categorize them into 3 logical sections (e.g., Luxury, Boutique, Budget).
        For each hotel, write 1-2 descriptive sentences.
        Include 'Travel Tips' at the end.
        Return ONLY JSON:
        {{
          "intro": "Intro about city hotels",
          "categories": [
            {{ "name": "Category Name", "hotels": [ {{ "name": "Hotel Name", "id": "HotelID", "desc": "Description" }} ] }}
          ],
          "tips": "City travel tips"
        }}
        """
        
        final_res = requests.post("https://api.groq.com/openai/v1/chat/completions", 
            headers={"Authorization": f"Bearer {random.choice(groq_keys)}"}, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": guide_prompt}], "response_format": {"type": "json_object"}}, timeout=15).json()
        
        guide = json.loads(final_res['choices'][0]['message']['content'])

        # 4. Собираем HTML с ТВОИМ форматом прямых ссылок
        html = f"<div style='line-height: 1.6; color: #333; font-family: sans-serif;'>"
        html += f"<p style='margin-bottom: 20px;'>{guide.get('intro')}</p>"

        for cat in guide.get('categories', []):
            html += f"<h3 style='color: #003580; margin-top: 25px; border-bottom: 2px solid #eee; padding-bottom: 5px;'>{cat['name']}</h3>"
            for h in cat.get('hotels', []):
                # ФОРМИРУЕМ ПРЯМУЮ ССЫЛКУ ПО ID (как в твоем примере)
                # https://www.stay22.com/allez/booking/{hotel_id}?aid=bstay24
                link = f"https://www.stay22.com/allez/booking/{h['id']}?aid={STAY22_AID}"
                
                html += f"""
                <div style='margin-bottom: 15px; padding: 12px; background: #fcfcfc; border-radius: 10px; border: 1px solid #eee;'>
                    <div style='display: flex; justify-content: space-between; align-items: flex-start; gap: 10px;'>
                        <strong style='font-size: 15px;'>{h['name']}</strong>
                        <a href='{link}' target='_blank' style='background: #007BFF; color: white; text-decoration: none; padding: 6px 14px; border-radius: 6px; font-size: 12px; font-weight: bold; white-space: nowrap;'>Book Now</a>
                    </div>
                    <p style='font-size: 13px; color: #555; margin: 8px 0 0 0;'>{h['desc']}</p>
                </div>"""

        html += f"<div style='background: #e9f7ef; padding: 15px; border-radius: 10px; margin-top: 20px;'><strong>💡 Советы по {city_name}:</strong><br><small>{guide.get('tips')}</small></div>"
        
        # Общая кнопка (поиск по городу)
        city_enc = urllib.parse.quote(city_name)
        html += f"<br><a href='https://www.stay22.com/allez/{STAY22_AID}?address={city_enc}' target='_blank' style='display: block; text-align: center; padding: 15px; background: #003580; color: white; text-decoration: none; border-radius: 10px; font-weight: bold;'>Посмотреть все варианты в {city_name}</a></div>"

        return JSONResponse(content={"reply": html})

    except Exception as e:
        return JSONResponse(content={"reply": f"Ошибка при создании гида: {str(e)}"})
