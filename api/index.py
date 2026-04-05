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
        # 1. Извлекаем чистый город на английском
        p_city = f"Extract city name in English from: '{msg}'. Respond ONLY with city name."
        c_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": p_city}]}, timeout=7)
        city_en = c_res.json()['choices'][0]['message']['content'].strip().replace(".", "")
        
        intent = "cheap" if any(x in msg for x in ["деш", "low", "бюдж"]) else "general"
        db_key = f"v6:store:{city_en.lower()}:{intent}"

        # 2. Берем текущий список из базы
        full_list = []
        if redis_db:
            raw = redis_db.get(db_key)
            full_list = json.loads(raw) if raw else []

        existing_ids = [item['id'] for item in full_list]
        
        # 3. Ищем 3 новых отеля
        new_items = get_new_hotels(city_en, intent, existing_ids)

        if new_items:
            g_prompt = f"""
            Напиши на русском гид по этим 3 отелям в {city_en}: {json.dumps(new_items)}. 
            Добавь один короткий лайфхак для туриста в этом городе.
            JSON ONLY: {{
              "adv": "совет дня",
              "cats": [ {{"id": "id", "n": "название", "cat": "почему стоит выбрать", "d": "описание 15 слов"}} ]
            }}"""
            g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
                json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": g_prompt}], "response_format": {"type": "json_object"}}, timeout=15)
            new_data = json.loads(g_res.json()['choices'][0]['message']['content'])
            
            # Сохраняем свежий совет и добавляем отели в начало
            current_advice = new_data.get('adv', 'Приятного путешествия!')
            for h in new_data['cats']:
                h['advice'] = current_advice
                full_list.insert(0, h)
            
            if redis_db: redis_db.set(db_key, json.dumps(full_list))

        if not full_list:
            return JSONResponse(content={"reply": "Отели не найдены. Попробуйте другой город."})

        # Показываем 10, остальные скрываем
        to_show = full_list[:10]
        hidden_count = len(full_list) - 10
        advice_text = to_show[0].get('advice', 'Бронируйте заранее для лучшей цены!')

        # --- СБОРКА НАРЯДНОГО HTML ---
        html = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 480px; margin: 0 auto; color: #1e272e;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; padding: 0 5px;">
                <div>
                    <span style="font-size: 22px; margin-right: 8px;">🇬🇧</span>
                    <b style="font-size: 20px; letter-spacing: -0.5px;">{city_en.capitalize()}</b>
                </div>
                <span style="background: #E3F2FD; color: #1976D2; padding: 4px 10px; border-radius: 20px; font-size: 11px; font-weight: 700;">{len(full_list)} ВАРИАНТОВ</span>
            </div>
        """
        
        for h in to_show:
            link = f"https://www.stay22.com/allez/booking/{h['id']}?aid={STAY22_AID}"
            html += f"""
            <div style="background: #ffffff; border-radius: 20px; padding: 20px; margin-bottom: 16px; border: 1px solid #f0f0f0; box-shadow: 0 10px 20px rgba(0,0,0,0.04); position: relative; overflow: hidden;">
                <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 12px;">
                    <div style="flex: 1;">
                        <div style="color: #0084FF; font-size: 10px; font-weight: 800; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 6px; display: flex; align-items: center; gap: 4px;">
                            <span style="width: 6px; height: 6px; background: #0084FF; border-radius: 50%;"></span> {h.get('cat', 'Рекомендация')}
                        </div>
                        <div style="font-size: 16px; font-weight: 700; color: #2d3436; margin-bottom: 8px; line-height: 1.3;">{h['n']}</div>
                    </div>
                    <a href="{link}" target="_blank" style="background: linear-gradient(135deg, #0084FF 0%, #00C6FF 100%); color: #fff; text-decoration: none; padding: 10px 20px; border-radius: 12px; font-size: 13px; font-weight: 700; box-shadow: 0 4px 15px rgba(0, 132, 255, 0.25);">Выбрать</a>
                </div>
                <div style="font-size: 13px; color: #636e72; line-height: 1.5; margin-top: 5px;">{h['d']}</div>
            </div>
            """
        
        # --- БЛОК СОВЕТА ---
        html += f"""
        <div style="background: linear-gradient(to right, #FFF9C4, #FFFDE7); border-radius: 16px; padding: 16px; margin: 24px 0; border: 1px dashed #FBC02D; display: flex; gap: 12px;">
            <div style="font-size: 20px;">💡</div>
            <div style="font-size: 13px; color: #5D4037; line-height: 1.4;">
                <b style="color: #AF8B00; display: block; margin-bottom: 2px;">Совет эксперта:</b>
                {advice_text}
            </div>
        </div>
        """

        all_link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(city_en)}"
        if hidden_count > 0:
            html += f"""
            <a href="{all_link}" target="_blank" style="display: block; text-align: center; padding: 16px; background: #f8f9fa; color: #1e272e; text-decoration: none; border-radius: 16px; font-weight: 700; font-size: 14px; border: 1px solid #e9ecef; margin-top: 10px;">
                Показать еще {hidden_count} отелей в {city_en.capitalize()} →
            </a>"""
        else:
            html += f"<a href='{all_link}' target='_blank' style='display: block; text-align:center; padding:16px; background: #1e272e; color: #fff; text-decoration:none; border-radius:16px; font-weight: 700; font-size: 14px;'>Смотреть все отели на карте →</a>"

        html += "</div>"
        return JSONResponse(content={"reply": html})
    except:
        return JSONResponse(content={"reply": "Ошибка. Попробуйте еще раз."})
