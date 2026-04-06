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
    try:
        headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": "booking-com18.p.rapidapi.com"}
        l_res = requests.get("https://booking-com18.p.rapidapi.com/stays/auto-complete", 
                             headers=headers, params={"query": city_en}, timeout=10)
        loc_data = l_res.json()['data'][0]
        dest_id = loc_data['id']
        country_code = loc_data.get('country', 'un').lower() # Код для flagcdn
        
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
        return new_found, country_code
    except: return [], "un"

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    msg = payload.message.strip().lower()
    g_key = random.choice(groq_keys)
    headers = {"Authorization": f"Bearer {g_key}"}

    try:
        p_city = f"Extract city name in English from: '{msg}'. Respond ONLY with city name."
        c_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": p_city}]}, timeout=7)
        city_en = c_res.json()['choices'][0]['message']['content'].strip().replace(".", "")
        
        intent = "cheap" if any(x in msg for x in ["деш", "low", "бюдж"]) else "general"
        db_key = f"v9:booking:{city_en.lower()}:{intent}"

        full_list = []
        country_code = "un"
        if redis_db:
            raw = redis_db.get(db_key)
            if raw:
                stored = json.loads(raw)
                full_list = stored.get('hotels', [])
                country_code = stored.get('country', 'un')

        existing_ids = [item['id'] for item in full_list]
        new_items, new_country = get_new_hotels(city_en, intent, existing_ids)
        if new_country != "un": country_code = new_country

        if new_items:
            g_prompt = f"Напиши на русском гид по 3 отелям в {city_en}: {json.dumps(new_items)}. Добавь совет туристу. JSON ONLY: {{'adv': 'совет', 'cats': [ {{'id': 'id', 'n': 'название', 'cat': 'тип', 'd': 'описание'}} ]}}"
            g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
                json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": g_prompt}], "response_format": {"type": "json_object"}}, timeout=15)
            new_data = json.loads(g_res.json()['choices'][0]['message']['content'])
            
            last_adv = new_data.get('adv', '')
            for h in new_data['cats']:
                h['advice'] = last_adv
                full_list.insert(0, h)
            
            if redis_db:
                redis_db.set(db_key, json.dumps({'hotels': full_list, 'country': country_code}))

        if not full_list: return JSONResponse(content={"reply": "Отели не найдены."})

        to_show = full_list[:10]
        hidden_count = len(full_list) - 10
        # Используем внешнюю картинку флага для надежности
        flag_url = f"https://flagcdn.com/w40/{country_code}.png"

        html = f"""
        <style>
            @keyframes slideUp {{ from {{ opacity: 0; transform: translateY(15px); }} to {{ opacity: 1; transform: translateY(0); }} }}
            .b-wrap {{ animation: slideUp 0.4s ease-out; width: 100%; font-family: 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; padding-bottom: 20px; }}
            .b-card {{ background: #fff; border: 1px solid #e7e7e7; border-radius: 8px; padding: 20px; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; gap: 20px; transition: box-shadow 0.2s; }}
            .b-card:hover {{ box-shadow: 0 4px 12px rgba(0,0,0,0.1); }}
        </style>
        <div class="b-wrap">
            <div style="max-width: 1000px; margin: 0 auto; padding: 15px;">
                <div style="height: 50px; display: flex; align-items: center; gap: 15px; margin-bottom: 10px;">
                    <img src="{flag_url}" width="35" style="border-radius: 4px; box-shadow: 0 1px 3px rgba(0,0,0,0.2);">
                    <h2 style="font-size: 24px; color: #003580; margin: 0;">{city_en.capitalize()}: {len(full_list)} вариантов</h2>
                </div>
        """
        
        for h in to_show:
            link = f"https://www.stay22.com/allez/booking/{h['id']}?aid={STAY22_AID}"
            html += f"""
            <div class="b-card">
                <div style="flex: 1;">
                    <div style="margin-bottom: 6px;">
                        <span style="background: #003580; color: #fff; font-size: 11px; font-weight: bold; padding: 3px 10px; border-radius: 4px; text-transform: uppercase;">{h.get('cat', 'Отель')}</span>
                    </div>
                    <div style="font-size: 19px; font-weight: bold; color: #006ce4; margin-bottom: 5px;">{h['n']}</div>
                    <div style="font-size: 13px; color: #4a4a4a; line-height: 1.5;">{h['d']}</div>
                </div>
                <div style="text-align: right;">
                    <div style="color: #008009; font-size: 12px; font-weight: 700; margin-bottom: 10px;">Бесплатная отмена</div>
                    <a href="{link}" target="_blank" style="background: #006ce4; color: #fff; text-decoration: none; padding: 12px 25px; border-radius: 4px; font-size: 14px; font-weight: bold; display: inline-block;">Показать цены</a>
                </div>
            </div>
            """
        
        if to_show[0].get('advice'):
            html += f"""
            <div style="background: #ebf3ff; border: 1px solid #003580; border-radius: 8px; padding: 18px; margin: 20px 0; display: flex; gap: 15px; align-items: center;">
                <div style="background: #003580; color: #fff; border-radius: 50%; min-width: 30px; height: 30px; display: flex; align-items: center; justify-content: center; font-weight: bold;">!</div>
                <div style="font-size: 14px; color: #003580;"><b>Совет туристам:</b> {to_show[0]['advice']}</div>
            </div>"""

        all_link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(city_en)}"
        txt = f"Загрузить ещё {hidden_count} отелей →" if hidden_count > 0 else "Посмотреть всё на карте"
        html += f"<a href='{all_link}' target='_blank' style='display: block; text-align: center; padding: 16px; background: #003580; color: #fff; text-decoration: none; border-radius: 4px; font-weight: bold; font-size: 15px;'>{txt}</a>"
        
        html += "</div></div>"
        return JSONResponse(content={"reply": html})
    except:
        return JSONResponse(content={"reply": "Ошибка. Попробуйте еще раз."})
