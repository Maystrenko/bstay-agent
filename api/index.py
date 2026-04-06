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
STAY22_AID = "btr" # Твоя партнерка

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
            if not isinstance(x, dict): continue # Броня от плохих данных
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
        p_city = f"Analyze the location in this text: '{msg}'. If it is a COUNTRY, respond ONLY with the word 'COUNTRY'. If it is a CITY, respond ONLY with the city name in English. Nothing else."
        c_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": p_city}]}, timeout=7)
        city_en = c_res.json()['choices'][0]['message']['content'].strip().replace(".", "").lower()
        
        if city_en == "country":
            return JSONResponse(content={"reply": "Вы указали целую страну 🌍. Пожалуйста, уточните, какой именно <b>город</b> вас интересует (например: столица или курорт)?"})
        
        if not city_en or "none" in city_en or len(city_en) < 2:
            return JSONResponse(content={"reply": "Пожалуйста, укажите конкретный город, например: Париж или Токио."})
        
        intent = "cheap" if any(x in msg for x in ["деш", "low", "бюдж"]) else "general"
        
        # v12 - чистая память!
        db_key = f"v12:booking:{city_en}:{intent}"
        lock_key = f"lock:v12:{city_en}:{intent}"

        full_list = []
        if redis_db:
            raw = redis_db.get(db_key)
            try:
                parsed = json.loads(raw) if raw else []
                # Броня: берем из базы только если это список
                full_list = parsed if isinstance(parsed, list) else []
            except:
                full_list = []

        if redis_db and not redis_db.get(lock_key):
            existing_ids = [item['id'] for item in full_list if isinstance(item, dict)]
            new_items = get_new_hotels(city_en, intent, existing_ids)

            if new_items:
                g_prompt = f"""
                У меня есть данные о 3 отелях в городе {city_en}: {json.dumps(new_items)}.
                Твоя задача — вернуть валидный JSON.
                ПРАВИЛО 1: В массив 'cats' скопируй данные отелей (id, название), а в 'd' напиши краткое описание.
                ПРАВИЛО 2: В поле 'adv' напиши ОДИН лайфхак для туриста (транспорт, еда, налоги).
                СУПЕР-ПРАВИЛО: Весь текст должен быть СТРОГО НА ЧИСТОМ РУССКОМ ЯЗЫКЕ. Категорически запрещены китайские, вьетнамские или другие иностранные слова!
                JSON ONLY: {{"adv": "текст", "cats": [ {{"id": "id", "n": "название", "cat": "ОТЕЛЬ", "d": "описание"}} ]}}
                """
                g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
                    json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": g_prompt}], "response_format": {"type": "json_object"}}, timeout=15)
                
                try:
                    new_data = json.loads(g_res.json()['choices'][0]['message']['content'])
                    last_adv = new_data.get('adv', '')
                    raw_cats = new_data.get('cats', [])
                    
                    # --- ГЛАВНАЯ БРОНЯ ОТ ГАЛЛЮЦИНАЦИЙ ИИ ---
                    if isinstance(raw_cats, dict): 
                        raw_cats = [raw_cats]
                    elif isinstance(raw_cats, str):
                        try: raw_cats = json.loads(raw_cats)
                        except: raw_cats = []
                        if isinstance(raw_cats, dict): raw_cats = [raw_cats]

                    for h in raw_cats:
                        if isinstance(h, dict): # Проверяем, что это точно словарь, а не буква
                            h['advice'] = last_adv
                            full_list.insert(0, h)
                    
                    if redis_db and full_list:
                        redis_db.set(db_key, json.dumps(full_list))
                        redis_db.set(lock_key, "1", ex=86400)
                except Exception as parse_e:
                    print(f"JSON Parse Error: {parse_e}")

        if not full_list: return JSONResponse(content={"reply": "Отели не найдены. Проверьте правильность написания города."})

        # --- ЛОГИКА ОТОБРАЖЕНИЯ ---
        display_limit = 5
        to_show = full_list[:display_limit]
        hidden_count = len(full_list) - display_limit

        html = f"""
        <div style="font-family: 'BlinkMacSystemFont', sans-serif; width: 100%; color: #1a1a1a; background: transparent; padding: 10px 0; box-sizing: border-box;">
            <div style="max-width: 1000px; margin: 0 auto; box-sizing: border-box;">
                <h2 style="font-size: 20px; font-weight: 700; color: #003580; margin-bottom: 15px; box-sizing: border-box;">{city_en.capitalize()}: {len(full_list)} вариантов найдено</h2>
        """
        
        for h in to_show:
            if not isinstance(h, dict): continue
            link = f"https://www.stay22.com/allez/booking/{h.get('id', '')}?aid={STAY22_AID}"
            html += f"""
            <div style="background: #ffffff; border: 1px solid #e7e7e7; border-radius: 8px; padding: 15px; margin-bottom: 12px; display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 15px; box-sizing: border-box;">
                <div style="flex: 1; min-width: 280px; box-sizing: border-box;">
                    <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 6px;">
                        <span style="background: #003580; color: #fff; font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 4px;">{h.get('cat', 'ОТЕЛЬ')}</span>
                        <span style="color: #008009; font-size: 12px; font-weight: 700;">✓ Проверено</span>
                    </div>
                    <div style="font-size: 18px; font-weight: 700; color: #006ce4; margin-bottom: 8px;">{h.get('n', 'Отель')}</div>
                    <div style="font-size: 13px; color: #4a4a4a; line-height: 1.5;">{h.get('d', '')}</div>
                </div>
                <div style="text-align: right; min-width: 150px; box-sizing: border-box;">
                    <a href="{link}" target="_blank" style="background: #006ce4; color: #ffffff; text-decoration: none; padding: 12px 24px; border-radius: 4px; font-size: 14px; font-weight: 600; display: inline-block; text-align: center; width: 100%; box-sizing: border-box;">Показать цены</a>
                </div>
            </div>
            """
        
        if len(to_show) > 0 and isinstance(to_show[0], dict) and to_show[0].get('advice'):
            html += f"""
            <div style="background: #ebf3ff; border: 1px solid #003580; border-radius: 8px; padding: 16px; margin: 20px 0; display: flex; align-items: center; gap: 15px; box-sizing: border-box;">
                <div style="background: #003580; color: #fff; border-radius: 50%; min-width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; font-weight: bold;">i</div>
                <div style="font-size: 14px; color: #003580; line-height: 1.5;"><b>💡 Совет эксперта по {city_en.capitalize()}:</b> {to_show[0]['advice']}</div>
            </div>"""

        all_link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(city_en)}"
        if hidden_count > 0:
            btn_label = f"Показать ещё {hidden_count} отелей →"
            btn_style = "background: #ffffff; color: #006ce4; border: 1px solid #006ce4;"
        else:
            btn_label = "Найти все варианты на карте →"
            btn_style = "background: #003580; color: #ffffff; border: none;"

        html += f"<a href='{all_link}' target='_blank' style='display: block; text-align: center; padding: 16px; text-decoration: none; border-radius: 4px; font-weight: 700; font-size: 15px; box-sizing: border-box; {btn_style}'>{btn_label}</a>"
        
        html += "</div></div>"
        return JSONResponse(content={"reply": html})
    except Exception as e:
        print(f"Error: {e}")
        return JSONResponse(content={"reply": f"Техническая ошибка: {str(e)}"})
