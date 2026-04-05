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

# Ключи
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

def get_hotels_list(city_name, lang='ru'):
    if not RAPID_API_KEY: return None, "No API Key"
    headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": RAPID_HOST}
    try:
        # 1. Поиск локации
        loc_res = requests.get(f"https://{RAPID_HOST}/stays/auto-complete", 
                               headers=headers, params={"query": city_name}, timeout=6)
        loc_json = loc_res.json()
        loc_list = loc_json if isinstance(loc_json, list) else loc_json.get('data', [])
        if not loc_list: return None, "City not found"
        dest_id = loc_list[0].get('id')

        # 2. Даты (30 дней вперед)
        in_d = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
        out_d = (datetime.now() + timedelta(days=33)).strftime('%Y-%m-%d')

        # 3. Поиск отелей
        params = {
            "locationId": dest_id, "checkinDate": in_d, "checkoutDate": out_d,
            "adults": "2", "rooms": "1", "units": "metric", "languagecode": lang, "currency_code": "USD"
        }
        res = requests.get(f"https://{RAPID_HOST}/stays/search", headers=headers, params=params, timeout=12)
        data = res.json()
        
        if isinstance(data, list): hotels = data
        else:
            d_block = data.get('data', {})
            hotels = d_block if isinstance(d_block, list) else (d_block.get('hotels', []) or d_block.get('results', []))
        
        return hotels[:10], None
    except Exception as e:
        return None, f"Err: {str(e)[:15]}"

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    try:
        t_lang = LANG_MAP.get(payload.lang, "Russian")
        prompt = f"Extract city (English) and write a short welcoming sentence in {t_lang}. User: {payload.message}. JSON ONLY: {{\"city\": \"City\", \"text\": \"Greeting\"}}"
        
        ai_res = None
        if groq_keys:
            try:
                g_key = random.choice(groq_keys)
                r = requests.post("https://api.groq.com/openai/v1/chat/completions", 
                    headers={"Authorization": f"Bearer {g_key}"}, 
                    json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "response_format": {"type": "json_object"}}, 
                    timeout=10)
                ai_res = r.json()['choices'][0]['message']['content']
            except: pass

        if not ai_res: return JSONResponse(content={"reply": "AI error."})
        
        ai_data = json.loads(ai_res[ai_res.find('{'):ai_res.rfind('}')+1])
        city = ai_data.get("city", "none")
        greeting = ai_data.get("text", "Searching...")

        list_html = ""
        if city.lower() != "none":
            hotels, err = get_hotels_list(city, payload.lang)
            if hotels:
                list_html = f"<div style='margin-top:15px; background:#fdfdfd; padding:15px; border-radius:12px; border:1px solid #ddd;'>"
                list_html += f"<h3 style='margin:0 0 12px 0; font-size:16px; color:#003580;'>🌟 Top 10 Hotels in {city}:</h3>"
                
                for i, h in enumerate(hotels, 1):
                    name = h.get('name') or h.get('hotel_name') or "Hotel"
                    
                    # Прямая ссылка Stay22 через поиск (самый надежный метод)
                    # Формат: Название + Город
                    search_query = urllib.parse.quote(f"{name} {city}")
                    link = f"https://www.stay22.com/allez/{STAY22_AID}?campaign=top10&address={search_query}"
                    
                    list_html += f"""
                    <div style='margin-bottom:10px; padding-bottom:8px; border-bottom:1px solid #eee; display:flex; justify-content:space-between; align-items:center;'>
                        <span style='font-size:14px; color:#333;'>{i}. <b>{name}</b></span>
                        <a href='{link}' target='_blank' style='background:#003580; color:white; text-decoration:none; padding:6px 14px; border-radius:6px; font-size:12px; font-weight:bold;'>Book</a>
                    </div>"""
                list_html += "</div>"

        # Кнопка общего поиска
        city_enc = urllib.parse.quote(city)
        main_link = f"https://www.stay22.com/allez/{STAY22_AID}?campaign=main_button&address={city_enc}"
        btn_text = f"🏨 Посмотреть все отели в {city}"
        
        footer = f"""
        <br><a href='{main_link}' target='_blank' style='display:inline-block; padding:16px; background:#007BFF; color:white; text-decoration:none; border-radius:10px; font-weight:bold; width:100%; text-align:center; box-sizing:border-box;'>{btn_text}</a>
        """

        return JSONResponse(content={"reply": greeting + list_html + footer})

    except Exception as e:
        return JSONResponse(content={"reply": f"Error: {str(e)}"})
