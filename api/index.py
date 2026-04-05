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
import redis as py_redis

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# --- ПОДКЛЮЧЕНИЕ К REDIS CLOUD ---
redis_db = None
try:
    url = os.environ.get("REDIS_URL")
    if url:
        redis_db = py_redis.from_url(url, decode_responses=True, socket_timeout=5)
except Exception as e:
    print(f"Redis Error: {e}")

groq_keys = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")
STAY22_AID = "bstay24"

class ChatPayload(BaseModel):
    message: str

def get_new_hotels(city_en, intent, existing_ids):
    """Ищем 3 отеля, которых НЕТ в нашем списке"""
    try:
        headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": "booking-com18.p.rapidapi.com"}
        l_res = requests.get("https://booking-com18.p.rapidapi.com/stays/auto-complete", 
                             headers=headers, params={"query": city_en}, timeout=10)
        dest_id = l_res.json()['data'][0]['id']
        
        params = {
            "locationId": dest_id, 
            "checkinDate": (datetime.now()+timedelta(days=30)).strftime('%Y-%m-%d'),
            "checkoutDate": (datetime.now()+timedelta(days=33)).strftime('%Y-%m-%d'),
            "adults": "2", "currency_code": "USD"
        }
        if intent == "cheap": params["sortBy"] = "price_lowest"
        
        h_res = requests.get("https://booking-com18.p.rapidapi.com/stays/search", headers=headers, params=params, timeout=15)
        data = h_res.json().get('data', [])
        if not isinstance(data, list): data = h_res.json().get('data', {}).get('hotels', [])
        
        new_found = []
        for x in data:
            h_id = str(x.get('hotel_id') or x.get('id'))
            if h_id not in existing_ids:
                new_found.append({"id": h_id, "name": x.get('name') or x.get('hotel_name')})
            if len(new_found) >= 3: break
        return new_found
    except: return []

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    msg = payload.message.strip().lower()
    g_key = random.choice(groq_keys)
    headers = {"Authorization": f"Bearer {g_key}"}

    try:
        # 1. Определяем город через ИИ (для точности)
        p_city = f"Extract city name in English from: '{msg}'. Respond ONLY with city name."
        c_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": p_city}]}, timeout=7)
        city_en = c_res.json()['choices'][0]['message']['content'].strip().replace(".", "")
        
        intent = "cheap" if any(x in msg for x in ["деш", "low", "бюдж"]) else "general"
        db_key = f"store:v4:{city_en.lower()}:{intent}"

        # 2. Достаем всё, что уже накопили в Redis
        full_list = []
        existing_ids = []
        if redis_db:
            raw = redis_db.get(db_key)
            if raw:
                full_list = json.loads(raw)
                existing_ids = [item['id'] for item in full_list]

        # 3. Добавляем 3 новых отеля сегодня
        new_items = get_new_hotels(city_en, intent, existing_ids)
        if new_items:
            g_prompt = f"Напиши на русском описание для 3 отелей в {city_en}: {json.dumps(new_items)}. JSON ONLY: {{'cats': [ {{'id': 'id', 'n': 'название', 'cat': 'почему он', 'd': 'описание 15 слов'}} ]}}"
            g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
                json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": g_prompt}], "response_format": {"type": "json_object"}}, timeout=15)
            new_data = json.loads(g_res.json()['choices'][0]['message']['content'])
            
            # Вставляем новые в начало списка
            for h in new_data['cats']:
                full_list.insert(0, h)
            
            # Сохраняем обновленный (выросший) список
            if redis_db:
                redis_db.set(db_key, json.dumps(full_list))

        if not full_list:
            return JSONResponse(content={"reply": "Отели не найдены."})

        # 4. ЛОГИКА ВЫДАЧИ: Показываем только ТОП-10
        display_limit = 10
        to_show = full_list[:display_limit]
        hidden_count = len(full_list) - display_limit

        # 5. Сборка HTML
        html = f"<div style='font-family:sans-serif; color:#333;'><p>📍 <b>{city_en.capitalize()}</b>: показываем {len(to_show)} из {len(full_list)} отелей.</p>"
        
        for h in to_show:
            link = f"https://www.stay22.com/allez/booking/{h['id']}?aid={STAY22_AID}"
            html += f"""
            <div style='margin-bottom:12px; padding:15px; background:#fff; border-radius:12px; border:1px solid #eef2f7; box-shadow:0 2px 4px rgba(0,0,0,0.02);'>
                <div style='display:flex; justify-content:space-between; align-items:flex-start;'>
                    <div style='max-width:70%;'>
                        <span style='font-size:10px; color:#007BFF; font-weight:bold;'>{h.get('cat', 'Выбор дня')}</span>
                        <div style='font-size:14px; font-weight:bold; color:#1a1a1a;'>{h['n']}</div>
                    </div>
                    <a href='{link}' target='_blank' style='background:#007BFF; color:#fff; text-decoration:none; padding:8px 14px; border-radius:8px; font-size:12px; font-weight:bold;'>Выбрать</a>
                </div>
                <p style='font-size:12px; color:#666; margin:8px 0 0;'>{h['d']}</p>
            </div>"""
        
        # Кнопка "Показать еще", если в базе больше 10
        all_link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(city_en)}"
        if hidden_count > 0:
            html += f"""
            <a href='{all_link}' target='_blank' style='display:block; text-align:center; padding:12px; background:#f0f7ff; color:#007BFF; text-decoration:none; border-radius:10px; font-weight:bold; font-size:13px; border:1px dashed #007BFF; margin-top:10px;'>
                Показать еще {hidden_count} отелей в {city_en.capitalize()} →
            </a>"""
        else:
            html += f"<br><a href='{all_link}' target='_blank' style='display:block; text-align:center; padding:12px; background:#003580; color:#fff; text-decoration:none; border-radius:10px; font-weight:bold;'>Смотреть все на карте →</a>"

        html += "</div>"
        return JSONResponse(content={"reply": html})
    except:
        return JSONResponse(content={"reply": "Ошибка. Попробуйте еще раз."})
