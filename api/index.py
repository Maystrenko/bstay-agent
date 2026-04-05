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

# Конфигурация ключей
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
    """Получение списка 10 отелей через RapidAPI"""
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

        # 2. Даты (на 30 дней вперед)
        in_d = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
        out_d = (datetime.now() + timedelta(days=33)).strftime('%Y-%m-%d')

        # 3. Поиск отелей
        params = {
            "locationId": dest_id, "checkinDate": in_d, "checkoutDate": out_d,
            "adults": "2", "rooms": "1", "units": "metric", "languagecode": lang, "currency_code": "USD"
        }
        res = requests.get(f"https://{RAPID_HOST}/stays/search", headers=headers, params=params, timeout=12)
        data = res.json()
        
        # Парсим список отелей
        if isinstance(data, list): hotels = data
        else:
            d_block = data.get('data', {})
            hotels = d_block if isinstance(d_block, list) else (d_block.get('hotels', []) or d_block.get('results', []))
        
        return hotels[:10], None # Возвращаем ровно 10
    except Exception as e:
        return None, f"Err: {str(e)[:15]}"

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    try:
        t_lang = LANG_MAP.get(payload.lang, "Russian")
        prompt = f"Extract city (English) and write a short welcoming sentence in {t_lang} about it. User: {payload.message}. JSON ONLY: {{\"city\": \"City\", \"text\": \"Greeting\"}}"
        
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
                list_html = f"<div style='margin-top:15px; background:#f9f9f9; padding:15px; border-radius:10px; border:1px solid #eee;'>"
                list_html += f"<h3 style='margin:0 0 10px 0; font-size:16px; color:#333;'>🌟 Top 10 Hotels in {city}:</h3>"
                
                for i, h in enumerate(hotels, 1):
                    name = h.get('name') or h.get('hotel_name') or "Hotel"
                    # Чистим цену
                    p_val = "0"
                    p_obj = h.get('price', {})
                    if isinstance(p_obj, dict):
                        gross = p_obj.get('amountPerStay', {}).get('grossAmount', {})
                        p_val = gross.get('amount') or gross.get('value') if isinstance(gross, dict) else p_obj.get('displayPrice', '0')
                    
                    price = "".join(filter(str.isdigit, str(p_val or h.get('minPrice', '0'))))
                    price_txt = f" — <b>${price}</b>" if price and price != "0" else ""
                    
                    # Ссылка Stay22 (Отель + Город)
                    link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(name + ', ' + city)}&campaign=top10_list"
                    
                    list_html += f"""
                    <div style='margin-bottom:12px; padding-bottom:8px; border-bottom:1px solid #e0e0e0; display:flex; justify-content:space-between; align-items:center;'>
                        <span style='font-size:14px; color:#333;'>{i}. <b>{name}</b>{price_txt}</span>
                        <a href='{link}' target='_blank' style='background:#007BFF; color:white; text-decoration:none; padding:5px 12px; border-radius:5px; font-size:12px; font-weight:bold; flex-shrink:0; margin-left:10px;'>Book</a>
                    </div>"""
                list_html += "</div>"

        # Финальная кнопка на Booking
        city_enc = urllib.parse.quote(city)
        main_link = f"https://www.stay22.com/allez/{STAY22_AID}?address={city_enc}&link=https://www.booking.com/searchresults.html?ss={city_enc}"
        btn_text = f"🏨 View All Hotels in {city}" if payload.lang == 'en' else f"🏨 Все отели в г. {city}"
        
        footer = f"""
        <br><a href='{main_link}' target='_blank' style='display:inline-block; padding:15px; background:#003580; color:white; text-decoration:none; border-radius:8px; font-weight:bold; width:100%; text-align:center; box-sizing:border-box;'>{btn_text}</a>
        """

        return JSONResponse(content={"reply": greeting + list_html + footer})

    except Exception as e:
        return JSONResponse(content={"reply": f"Error: {str(e)}"})
