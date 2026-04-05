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
    """Получение 10 отелей для последующей обработки ИИ"""
    if not RAPID_API_KEY: return None, "No API Key"
    headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": RAPID_HOST}
    try:
        loc_res = requests.get(f"https://{RAPID_HOST}/stays/auto-complete", headers=headers, params={"query": city_name}, timeout=6)
        loc_json = loc_res.json()
        loc_list = loc_json if isinstance(loc_json, list) else loc_json.get('data', [])
        if not loc_list: return None, "City not found"
        dest_id = loc_list[0].get('id')

        in_d = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
        out_d = (datetime.now() + timedelta(days=33)).strftime('%Y-%m-%d')

        params = {"locationId": dest_id, "checkinDate": in_d, "checkoutDate": out_d, "adults": "2", "rooms": "1", "units": "metric", "languagecode": lang, "currency_code": "USD"}
        res = requests.get(f"https://{RAPID_HOST}/stays/search", headers=headers, params=params, timeout=12)
        data = res.json()
        
        hotels = []
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
        
        # 1. Сначала узнаем город
        city_prompt = f"Extract only city name in English from: '{payload.message}'. Return JSON: {{\"city\": \"City\"}}"
        ai_city_res = requests.post("https://api.groq.com/openai/v1/chat/completions", 
            headers={"Authorization": f"Bearer {random.choice(groq_keys)}"}, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": city_prompt}], "response_format": {"type": "json_object"}}, timeout=10).json()
        
        city = ai_city_res['choices'][0]['message']['content']
        city_name = json.loads(city).get("city", "none")

        if city_name.lower() == "none":
            return JSONResponse(content={"reply": "Пожалуйста, укажите город, чтобы я мог составить гид по отелям."})

        # 2. Получаем реальные отели
        hotels, err = get_hotels_data(city_name, payload.lang)
        if not hotels:
            return JSONResponse(content={"reply": f"Не удалось найти отели в {city_name}. Попробуйте другой город."})

        # Готовим список отелей для ИИ
        hotels_names = [h.get('name') or h.get('hotel_name') or "Hotel" for h in hotels]
        
        # 3. Просим ИИ составить расширенный гид
        guide_prompt = f"""
        Based on this list of real hotels in {city_name}: {hotels_names}.
        Create a detailed travel guide in {t_lang}.
        Categorize them into 3 sections: 'Luxury', 'Boutique/Modern', and 'Budget/Convenience'.
        For each section, pick relevant hotels from the list.
        Write 1-2 descriptive sentences for each hotel.
        Include a 'Travel Tips' section at the end for {city_name}.
        Return JSON structure: 
        {{
          "intro": "General intro about city hotels",
          "categories": [
            {{ "name": "Category Name", "hotels": [ {{ "name": "Hotel Name", "desc": "Description" }} ] }}
          ],
          "tips": "3 travel tips for this city"
        }}
        """
        
        guide_res = requests.post("https://api.groq.com/openai/v1/chat/completions", 
            headers={"Authorization": f"Bearer {random.choice(groq_keys)}"}, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": guide_prompt}], "response_format": {"type": "json_object"}}, timeout=15).json()
        
        guide_data = json.loads(guide_res['choices'][0]['message']['content'])

        # 4. Собираем HTML
        full_html = f"<div style='font-family: sans-serif; line-height: 1.5; color: #333;'>"
        full_html += f"<p style='margin-bottom: 20px;'>{guide_data.get('intro')}</p>"

        for cat in guide_data.get('categories', []):
            full_html += f"<h3 style='color: #003580; border-bottom: 2px solid #003580; padding-bottom: 5px; margin-top: 25px;'>{cat['name']}</h3>"
            for h_info in cat.get('hotels', []):
                h_name = h_info['name']
                h_desc = h_info['desc']
                # Формируем ссылку Stay22
                link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(h_name + ', ' + city_name)}&campaign=expanded_guide"
                
                full_html += f"""
                <div style='margin-bottom: 15px; padding: 10px; background: #f9f9f9; border-radius: 8px;'>
                    <div style='display: flex; justify-content: space-between; align-items: flex-start;'>
                        <span style='font-weight: bold; font-size: 15px;'>{h_name}</span>
                        <a href='{link}' target='_blank' style='background: #007BFF; color: white; text-decoration: none; padding: 4px 12px; border-radius: 6px; font-size: 12px; font-weight: bold;'>Book</a>
                    </div>
                    <p style='font-size: 13px; color: #666; margin: 5px 0 0 0;'>{h_desc}</p>
                </div>"""

        full_html += f"<h3 style='color: #28a745; margin-top: 25px;'>📍 Советы по поездке:</h3>"
        full_html += f"<p style='font-size: 13px; font-style: italic; background: #e9f7ef; padding: 10px; border-radius: 8px;'>{guide_data.get('tips')}</p>"
        
        city_enc = urllib.parse.quote(city_name)
        main_link = f"https://www.stay22.com/allez/{STAY22_AID}?address={city_enc}&link=https://www.booking.com/searchresults.html?ss={city_enc}"
        full_html += f"<br><a href='{main_link}' target='_blank' style='display: block; text-align: center; padding: 15px; background: #003580; color: white; text-decoration: none; border-radius: 10px; font-weight: bold;'>Смотреть все варианты в {city_name}</a></div>"

        return JSONResponse(content={"reply": full_html})

    except Exception as e:
        return JSONResponse(content={"reply": f"Ошибка при создании гида: {str(e)}"})
