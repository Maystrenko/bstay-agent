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

def get_hotels_safe(city_name, lang='ru'):
    if not RAPID_API_KEY: return None, "No API Key"
    headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": RAPID_HOST}
    
    try:
        # 1. Поиск локации (stays/auto-complete)
        loc_res = requests.get(f"https://{RAPID_HOST}/stays/auto-complete", 
                               headers=headers, params={"query": city_name}, timeout=6)
        
        if loc_res.status_code != 200:
            return None, f"Loc Http {loc_res.status_code}"
            
        raw_loc = loc_res.json()
        
        # Исправляем ошибку 'list' object has no attribute 'get'
        if isinstance(raw_loc, list):
            loc_data = raw_loc
        else:
            loc_data = raw_loc.get('data', [])

        if not loc_data: return None, "City not found"
        
        # Берем ID первого результата
        dest_id = loc_data[0].get('id')
        if not dest_id: return None, "No ID in loc"

        # 2. Даты
        checkin = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
        checkout = (datetime.now() + timedelta(days=33)).strftime('%Y-%m-%d')

        # 3. Поиск отелей (stays/search)
        search_params = {
            "locationId": dest_id,
            "checkinDate": checkin,
            "checkoutDate": checkout,
            "adults": "2",
            "rooms": "1",
            "units": "metric",
            "languagecode": lang,
            "currency_code": "USD"
        }
        
        search_res = requests.get(f"https://{RAPID_HOST}/stays/search", 
                                  headers=headers, params=search_params, timeout=10)
        
        if search_res.status_code != 200:
            return None, f"Search Http {search_res.status_code}"

        search_data = search_res.json()
        
        # Защита от пустых данных
        hotels = []
        if isinstance(search_data, list):
            hotels = search_data
        elif isinstance(search_data, dict):
            # В разных версиях API отели могут быть в разных ключах
            data_block = search_data.get('data', {})
            if isinstance(data_block, list):
                hotels = data_block
            else:
                hotels = data_block.get('hotels', []) or data_block.get('results', [])

        if not hotels: return None, "No hotels found"
            
        return hotels[:3], None
        
    except Exception as e:
        # Теперь мы увидим точную ошибку, если она случится
        return None, f"Err: {str(e)[:20]}"

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    try:
        t_lang = LANG_MAP.get(payload.lang, "Russian")
        prompt = f"Extract city (English) and write 2-sentence greeting in {t_lang}. User: {payload.message}. Return JSON: {{\"city\": \"City\", \"text\": \"Greeting\"}}"
        
        ai_res = None
        engine = "None"

        if groq_keys:
            try:
                g_key = random.choice(groq_keys)
                r = requests.post("https://api.groq.com/openai/v1/chat/completions", 
                    headers={"Authorization": f"Bearer {g_key}"}, 
                    json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "response_format": {"type": "json_object"}}, 
                    timeout=10)
                ai_res = r.json()['choices'][0]['message']['content']
                engine = "Groq"
            except: pass

        if not ai_res: return JSONResponse(content={"reply": "AI error."})

        data = json.loads(ai_res[ai_res.find('{'):ai_res.rfind('}')+1])
        city = data.get("city", "none")
        greeting = data.get("text", "Готово!")

        hotels_html = ""
        api_info = "Live"
        
        if city.lower() != "none" and len(city) > 2:
            hotels, err = get_hotels_safe(city, payload.lang)
            if hotels:
                hotels_html = "<div style='margin-top:15px; display:flex; flex-direction:column; gap:10px;'>"
                for h in hotels:
                    name = h.get('name') or h.get('hotel_name', 'Hotel')
                    
                    # Безопасное извлечение цены
                    price_val = "?"
                    p_obj = h.get('price', {})
                    if isinstance(p_obj, dict):
                        price_val = p_obj.get('displayPrice') or p_obj.get('amount') or "?"
                    
                    price = "".join(filter(str.isdigit, str(price_val))) or "?"
                    img = h.get('mainPhotoUrl') or h.get('main_photo_url', '')
                    
                    link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(name)}"
                    
                    hotels_html += f"""
                    <div style='background:#fff; border:1px solid #eee; border-radius:12px; overflow:hidden; box-shadow:0 4px 10px rgba(0,0,0,0.1);'>
                        {f"<img src='{img}' style='width:100%; height:130px; object-fit:cover;'>" if img else ""}
                        <div style='padding:12px;'>
                            <div style='font-weight:bold; font-size:14px;'>{name}</div>
                            <div style='font-size:12px; color:#28a745; margin:5px 0; font-weight:bold;'>от {price} USD за 3 ночи</div>
                            <a href='{link}' target='_blank' style='display:block; text-align:center; padding:10px; background:#007BFF; color:white; text-decoration:none; border-radius:6px; font-weight:bold; font-size:12px;'>Выбрать номер</a>
                        </div>
                    </div>"""
                hotels_html += "</div>"
            if err: api_info = err

        city_enc = urllib.parse.quote(city)
        main_url = f"https://www.stay22.com/allez/{STAY22_AID}?address={city_enc}&link=https://www.booking.com/searchresults.html?ss={city_enc}"
        btn_text = f"🏨 Все отели в {city}" if payload.lang == 'ru' else f"🏨 View hotels in {city}"
        
        footer = f"<br><a href='{main_url}' target='_blank' style='display:inline-block; padding:15px; background:#003580; color:white; text-decoration:none; border-radius:8px; font-weight:bold; width:100%; text-align:center; box-sizing:border-box;'>{btn_text}</a><br><small style='color:gray; font-size:9px;'>Engine: {engine} | API: {api_info}</small>"

        return JSONResponse(content={"reply": greeting + hotels_html + footer})

    except Exception as e:
        return JSONResponse(content={"reply": f"System error: {str(e)}"})
