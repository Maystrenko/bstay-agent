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
        p_city = f"Extract city name in English from: '{msg}'. Respond ONLY with city name."
        c_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": p_city}]}, timeout=7)
        city_en = c_res.json()['choices'][0]['message']['content'].strip().replace(".", "")
        
        intent = "cheap" if any(x in msg for x in ["деш", "low", "бюдж"]) else "general"
        db_key = f"v5:store:{city_en.lower()}:{intent}"

        full_list = []
        if redis_db:
            raw = redis_db.get(db_key)
            full_list = json.loads(raw) if raw else []

        existing_ids = [item['id'] for item in full_list]
        new_items = get_new_hotels(city_en, intent, existing_ids)

        if new_items:
            g_prompt = f"""
            Напиши на русском гид по этим 3 отелям в {city_en}: {json.dumps(new_items)}. 
            Также дай один короткий полезный совет путешественнику.
            JSON ONLY: {{
              "adv": "совет по городу",
              "cats": [ {{"id": "id", "n": "название", "cat": "почему он", "d": "описание 15 слов"}} ]
            }}"""
            g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
                json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": g_prompt}], "response_format": {"type": "json_object"}}, timeout=15)
            new_data = json.loads(g_res.json()['choices'][0]['message']['content'])
            
            # Сохраняем совет только от последней выдачи
            last_advice = new_data.get('adv', '')
            for h in new_data['cats']:
                h['advice'] = last_advice # Привязываем совет к метаданным
                full_list.insert(0, h)
            
            if redis_db: redis_db.set(db_key, json.dumps(full_list))

        if not full_list:
            return JSONResponse(content={"reply": "Отели не найдены."})

        # Логика 10 отелей
        to_show = full_list[:10]
        hidden = len(full_list) - 10
        current_advice = to_show[0].get('advice', '')

        # Сборка HTML
        html = f"""
        <div style="font-family: 'Segoe UI', Roboto, sans-serif; max-width: 450px; margin: 0 auto; color: #2d3436;">
            <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 15px;">
                <span style="font-size: 20px;">📍</span>
                <b style="font-size: 18px; color: #1e272e;">{city_en.capitalize()}</b>
                <span style="background: #f1f2f6; padding: 2px 8px; border-radius: 10px; font-size: 12px; color: #747d8c;">{len(full_list)} вариантов</span>
            </div>
        """
        
        for h in to_show:
            link = f"https://www.stay22.com/allez/booking/{h['id']}?aid={STAY22_AID}"
            html += f"""
            <div style="background: #ffffff; border: 1px solid #f1f2f6; border-radius: 16px; padding: 16px; margin-bottom: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.03);">
                <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 10px;">
                    <div>
                        <div style="color: #0984e3; font-size: 10px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.8px; margin-bottom: 4px;">{h.get('cat', 'Рекомендуем')}</div>
                        <div style="font-size: 15px; font-weight: 700; color: #2d3436; line-height: 1.3;">{h['n']}</div>
                    </div>
                    <a href="{link}" target="_blank" style="background: linear-gradient(135deg, #0984e3, #00cec9); color: #fff; text-decoration: none; padding: 8px 16px; border-radius: 10px; font-size: 12px; font-weight: 700; box-shadow: 0 4px 10px rgba(9, 132, 227, 0.2);">Выбрать</a>
                </div>
                <div style="font-size: 13px; color: #636e72; margin-top: 10px; line-height: 1.5;">{h['d']}</div>
            </div>
            """
        
        # Блок СОВЕТА
        if current_advice:
            html += f"""
            <div style="background: #fff9e6; border: 1px solid #ffeaa7; border-radius: 12px; padding: 12px; margin: 20px 0; display: flex; gap: 10px; align-items: flex-start;">
                <span style="font-size: 18px;">💡</span>
                <div style="font-size: 13px; color: #d6a031; line-height: 1.4;">
                    <b style="color: #b8860b; display: block; margin-bottom: 2px;">Полезный совет:</b>
                    {current_advice}
                </div>
            </div>
            """

        all_link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(city_en)}"
        if hidden > 0:
            html += f"""
            <a href="{all_link}" target="_blank" style="display: block; text-align: center; padding: 14px; background: #f1f2f6; color: #2d3436; text-decoration: none; border-radius: 12px; font-weight: 700; font-size: 13px; border: 1px solid #dfe6e9;">
                Показать еще {hidden} вариантов в {city_en.capitalize()} →
            </a>"""
        else:
            html += f"<a href='{all_link}' target='_blank' style='display: block; text-align:center; padding:14px; background: #2d3436; color: #fff; text-decoration:none; border-radius:12px; font-weight: 700;'>Смотреть всё на карте →</a>"

        html += "</div>"
        return JSONResponse(content={"reply": html})
    except:
        return JSONResponse(content={"reply": "Ошибка. Попробуйте еще раз."})
