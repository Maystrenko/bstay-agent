import os
import json
import urllib.parse
import random
import requests
import time
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

HOTEL_CACHE = {}
CACHE_TTL = 21600 

class ChatPayload(BaseModel):
    message: str
    lang: str = "ru"
    chat_history: list = []

def get_hotels(city):
    """Поиск отелей с проверкой на пустые данные"""
    try:
        headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": RAPID_HOST}
        # 1. Автокомплит
        l_res = requests.get(f"https://{RAPID_HOST}/stays/auto-complete", headers=headers, params={"query": city}, timeout=7)
        l_data = l_res.json().get('data', [])
        if not l_data: return None
        dest_id = l_data[0]['id']
        
        # 2. Поиск
        params = {
            "locationId": dest_id, 
            "checkinDate": (datetime.now()+timedelta(days=30)).strftime('%Y-%m-%d'), 
            "checkoutDate": (datetime.now()+timedelta(days=33)).strftime('%Y-%m-%d'), 
            "adults": "2", "currency_code": "USD"
        }
        h_res = requests.get(f"https://{RAPID_HOST}/stays/search", headers=headers, params=params, timeout=10)
        h_json = h_res.json()
        raw = h_json.get('data', [])
        
        # Обработка разных форматов ответа Booking
        if not isinstance(raw, list): 
            raw = h_json.get('data', {}).get('hotels', []) or h_json.get('data', {}).get('results', [])
        
        if not raw: return None
        return [{"id": str(h.get('hotel_id') or h.get('id')), "name": h.get('name') or h.get('hotel_name')} for h in raw if (h.get('id') or h.get('hotel_id'))][:10]
    except Exception as e:
        print(f"API ERROR: {e}")
        return None

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    current_time = time.time()
    msg = payload.message.strip()
    
    try:
        g_key = random.choice(groq_keys)
        headers = {"Authorization": f"Bearer {g_key}"}

        # --- ШАГ 1: ОПРЕДЕЛЯЕМ ГОРОД ---
        # Если в сообщении 1-2 слова (например "лондон" или "Лондон дешево"), берем его как город напрямую
        words = msg.split()
        if len(words) <= 2:
            city = msg.lower()
        else:
            # Если фраза длинная, просим ИИ вытащить город
            c_prompt = [{"role": "system", "content": "Extract city in English. JSON: {'c': 'City'}. If no city, 'none'."}]
            c_prompt.extend(payload.chat_history[-2:]) # Минимум истории для точности
            c_prompt.append({"role": "user", "content": msg})
            
            c_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
                json={"model": "llama-3.3-70b-versatile", "messages": c_prompt, "response_format": {"type": "json_object"}}, timeout=7)
            city = json.loads(c_res.json()['choices'][0]['message']['content']).get("c", "none").strip().lower()

        if city == "none":
            return JSONResponse(content={"reply": "Напишите, пожалуйста, название города (например: Лондон)."})

        # --- ШАГ 2: КЭШ ---
        cache_key = f"{city}_{payload.lang}"
        if cache_key in HOTEL_CACHE and (current_time - HOTEL_CACHE[cache_key]['timestamp'] < CACHE_TTL):
            return JSONResponse(content={"reply": HOTEL_CACHE[cache_key]['html']})

        # --- ШАГ 3: ПОЛУЧАЕМ ОТЕЛИ ---
        hotels = get_hotels(city)
        if not hotels:
            return JSONResponse(content={"reply": f"Не удалось найти отели в '{city}'. Попробуйте другой город."})

        # --- ШАГ 4: ГЕНЕРИРУЕМ КРАСИВЫЙ ГИД ---
        g_prompt = f"Create a travel guide for {city} in Russian. Use hotels: {json.dumps(hotels)}. Format: JSON with 'i' (intro), 'cats' (list with 'n' for category and 'h' for hotel {{\"id\", \"n\", \"d\"}}), 't' (tips)."
        g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": g_prompt}], "response_format": {"type": "json_object"}}, timeout=12)
        g = json.loads(g_res.json()['choices'][0]['message']['content'])

        # Сборка верстки
        html = f"<div style='font-family: Karla, sans-serif;'><p>{g['i']}</p>"
        for cat in g['cats']:
            # Обработка случая, когда 'h' может быть списком или объектом (один отель на рубрику)
            h_list = cat['h'] if isinstance(cat['h'], list) else [cat['h']]
            html += f"<h4 style='color:#003580; margin:15px 0 5px; border-bottom:1px solid #eee;'>{cat['n']}</h4>"
            for h in h_list:
                link = f"https://www.stay22.com/allez/booking/{h['id']}?aid={STAY22_AID}"
                html += f"""
                <div style='margin-bottom:10px; padding:10px; background:#fff; border-radius:10px; border:1px solid #eee; display:flex; justify-content:space-between; align-items:center;'>
                    <div style='padding-right:10px;'><b style='font-size:14px;'>{h['n']}</b><br><small style='color:#666; font-size:12px;'>{h['d']}</small></div>
                    <a href='{link}' target='_blank' style='background:#007BFF; color:#fff; text-decoration:none; padding:7px 14px; border-radius:6px; font-weight:bold; font-size:12px;'>Book</a>
                </div>"""
        
        html += f"<div style='background:#eef5ff; padding:12px; border-radius:8px; margin-top:15px; font-size:13px;'><b>💡 Лайфхак:</b> {g['t']}</div></div>"

        HOTEL_CACHE[cache_key] = {"timestamp": current_time, "html": html}
        return JSONResponse(content={"reply": html})

    except Exception as e:
        # Если всё упало, выдаем хотя бы город, который поняли
        return JSONResponse(content={"reply": f"Извините, произошла ошибка. Но я понял, что вы ищете {city.capitalize()}. Попробуйте еще раз через минуту."})
