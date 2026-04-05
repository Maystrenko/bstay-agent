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

# Пытаемся импортировать SDK Gemini (на случай, если захочешь использовать его)
try:
    from google import genai
    SDK_AVAILABLE = True
except:
    SDK_AVAILABLE = False

app = FastAPI()

# Разрешаем CORS, чтобы фронтенд мог общаться с бэкендом
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Читаем ключи из настроек Vercel
gemini_keys = [k.strip() for k in os.environ.get("GEMINI_API_KEY", "").split(",") if k.strip()]
groq_keys = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")

# Настройки проекта
RAPID_HOST = "booking-com18.p.rapidapi.com"
STAY22_AID = "bstay24"
LANG_MAP = {'ru': 'Russian', 'en': 'English', 'de': 'German', 'fr': 'French', 'es': 'Spanish'}

class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list
    lang: str = "en"

def get_hotels_safe(city_name, lang='ru'):
    """Поиск отелей через stays/auto-complete и stays/search (версия 18)"""
    if not RAPID_API_KEY: return None, "No API Key"
    headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": RAPID_HOST}
    
    try:
        # 1. Поиск ID локации
        loc_res = requests.get(f"https://{RAPID_HOST}/stays/auto-complete", 
                               headers=headers, params={"query": city_name}, timeout=6)
        loc_json = loc_res.json()
        
        # Защита от 'list' object error (если пришел список вместо словаря)
        loc_list = loc_json if isinstance(loc_json, list) else loc_json.get('data', [])
        if not loc_list: return None, "City not found"
        
        dest_id = loc_list[0].get('id')
        if not dest_id: return None, "No Location ID"

        # 2. Формируем даты (30 дней вперед на 3 ночи)
        in_date = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
        out_date = (datetime.now() + timedelta(days=33)).strftime('%Y-%m-%d')

        # 3. Поиск отелей
        search_params = {
            "locationId": dest_id,
            "checkinDate": in_date,
            "checkoutDate": out_date,
            "adults": "2",
            "rooms": "1",
            "units": "metric",
            "languagecode": lang,
            "currency_code": "USD"
        }
        
        search_res = requests.get(f"https://{RAPID_HOST}/stays/search", 
                                  headers=headers, params=search_params, timeout=12)
        search_json = search_res.json()

        # Глубокий парсинг отелей (список может быть в разных местах)
        hotels = []
        if isinstance(search_json, list):
            hotels = search_json
        else:
            d_block = search_json.get('data', {})
            if isinstance(d_block, list):
                hotels = d_block
            else:
                hotels = d_block.get('hotels', []) or d_block.get('results', [])

        if not hotels: return None, "No hotels available"
        return hotels[:3], None
        
    except Exception as e:
        return None, f"SysErr: {str(e)[:15]}"

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    try:
        t_lang = LANG_MAP.get(payload.lang, "Russian")
        prompt = f"""
        Extract city in English and write 2-sentence cool greeting in {t_lang}. 
        User message: "{payload.message}"
        Return ONLY JSON: {{"city": "City", "text": "Greeting"}}
        """
        
        ai_res = None
        engine = "None"

        # Пробуем Groq (самый надежный для твоего проекта сейчас)
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

        if not ai_res:
            return JSONResponse(content={"reply": "ИИ временно недоступен. Попробуйте еще раз."})

        # Парсим город и текст
        data = json.loads(ai_res[ai_res.find('{'):ai_res.rfind('}')+1])
        city = data.get("city", "none")
        greeting = data.get("text", "Searching...")

        hotels_html = ""
        api_info = "Live"
        
        if city.lower() != "none" and len(city) > 2:
            hotels, err = get_hotels_safe(city, payload.lang)
            if hotels:
                hotels_html = "<div style='margin-top:15px; display:flex; flex-direction:column; gap:12px;'>"
                for h in hotels:
                    # Чистим Имя
                    name = h.get('name') or h.get('hotel_name', 'Great Hotel')
                    
                    # Чистим Цену (ищем во всех ключах версии 18)
                    p_info = h.get('priceDetails') or h.get('price') or {}
                    gross = p_info.get('grossAmount', {})
                    p_val = "0"
                    if isinstance(gross, dict):
                        p_val = gross.get('value') or gross.get('amount')
                    else:
                        p_val = gross or p_info.get('displayPrice') or h.get('min_total_price', '0')
                    
                    price = "".join(filter(str.isdigit, str(p_val))) or "?"
                    
                    # Чистим Фото
                    img = h.get('mainPhotoUrl') or h.get('main_photo_url')
                    if not img and h.get('wishlist_data'):
                        img = h['wishlist_data'].get('main_photo_url')
                    
                    if img and 'square60' in img:
                        img = img.replace('square60', 'square300')
                    
                    link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(name)}&campaign=ai_card"
                    
                    hotels_html += f"""
                    <div style='background:#fff; border:1px solid #eee; border-radius:12px; overflow:hidden; box-shadow:0 4px 12px rgba(0,0,0,0.1);'>
                        {f"<img src='{img}' style='width:100%; height:160px; object-fit:cover; display:block;'>" if img else "<div style='height:120px; background:#f0f0f0;'></div>"}
                        <div style='padding:12px;'>
                            <div style='font-weight:bold; font-size:15px; color:#333; line-height:1.2;'>{name}</div>
                            <div style='font-size:13px; color:#28a745; margin:8px 0; font-weight:bold;'>от {price} USD за 3 ночи</div>
                            <a href='{link}' target='_blank' style='display:block; text-align:center; padding:12px; background:#007BFF; color:white; text-decoration:none; border-radius:8px; font-weight:bold; font-size:13px;'>Выбрать номер</a>
                        </div>
                    </div>"""
                hotels_html += "</div>"
            if err: api_info = err

        # Финальная большая кнопка и подпись
        city_enc = urllib.parse.quote(city)
        main_url = f"https://www.stay22.com/allez/{STAY22_AID}?address={city_enc}&link=https://www.booking.com/searchresults.html?ss={city_enc}"
        btn_text = f"🏨 Все отели в {city}" if payload.lang == 'ru' else f"🏨 View all in {city}"
        
        footer = f"""
        <br><a href='{main_url}' target='_blank' style='display:inline-block; padding:16px; background:#003580; color:white; text-decoration:none; border-radius:10px; font-weight:bold; width:100%; text-align:center; box-sizing:border-box;'>{btn_text}</a>
        <br><small style='color:gray; font-size:9px;'>Engine: {engine} | {api_info}</small>
        """

        return JSONResponse(content={"reply": greeting + hotels_html + footer})

    except Exception as e:
        return JSONResponse(content={"reply": f"Ошибка системы: {str(e)}"})
