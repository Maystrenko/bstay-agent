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

redis_db = None
try:
    url = os.environ.get("REDIS_URL")
    if url: redis_db = py_redis.from_url(url, decode_responses=True, socket_timeout=5)
except Exception as e: print(f"Redis Error: {e}")

groq_keys = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")
STAY22_AID = "btr"

class ChatPayload(BaseModel):
    message: str
    lang: str = "en" # Теперь принимаем язык от фронтенда!

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
            if not isinstance(x, dict): continue
            h_id = str(x.get('hotel_id') or x.get('id'))
            if h_id not in existing_ids:
                new_found.append({"id": h_id, "name": x.get('name') or x.get('hotel_name')})
            if len(new_found) >= 3: break
        return new_found
    except: return []

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    msg = payload.message.strip().lower()
    user_lang = payload.lang.lower()[:2] # Берем код языка (ru, es, de...)
    
    g_key = random.choice(groq_keys)
    headers = {"Authorization": f"Bearer {g_key}"}

    # МУЛЬТИЯЗЫЧНЫЕ КНОПКИ И ОШИБКИ ДЛЯ 10 ЯЗЫКОВ
    UI_LANGS = {
        'ru': {'err_country': "Вы указали целую страну 🌍. Пожалуйста, уточните <b>город</b>.", 'err_city': "Пожалуйста, укажите конкретный город.", 'not_found': "Отели не найдены.", 'found': "вариантов найдено", 'hotel': "ОТЕЛЬ", 'verified': "✓ Проверено", 'show_prices': "Показать цены", 'advice_title': "💡 Совет эксперта по", 'show_more': "Показать ещё", 'hotels_more': "отелей →", 'show_all': "Найти все варианты на карте →"},
        'en': {'err_country': "You specified a whole country 🌍. Please specify a <b>city</b>.", 'err_city': "Please specify a specific city.", 'not_found': "No hotels found.", 'found': "options found", 'hotel': "HOTEL", 'verified': "✓ Verified", 'show_prices': "Show prices", 'advice_title': "💡 Expert advice for", 'show_more': "Show", 'hotels_more': "more hotels →", 'show_all': "Find all options on map →"},
        'es': {'err_country': "Especificaste un país entero 🌍. Por favor, especifica una <b>ciudad</b>.", 'err_city': "Por favor, especifica una ciudad.", 'not_found': "No se encontraron hoteles.", 'found': "opciones encontradas", 'hotel': "HOTEL", 'verified': "✓ Verificado", 'show_prices': "Ver precios", 'advice_title': "💡 Consejo de experto para", 'show_more': "Mostrar", 'hotels_more': "hoteles más →", 'show_all': "Ver todas las opciones en el mapa →"},
        'de': {'err_country': "Sie haben ein ganzes Land angegeben 🌍. Bitte geben Sie eine <b>Stadt</b> an.", 'err_city': "Bitte geben Sie eine bestimmte Stadt an.", 'not_found': "Keine Hotels gefunden.", 'found': "Optionen gefunden", 'hotel': "HOTEL", 'verified': "✓ Überprüft", 'show_prices': "Preise anzeigen", 'advice_title': "💡 Expertentipp für", 'show_more': "Zeige", 'hotels_more': "weitere Hotels →", 'show_all': "Alle Optionen auf der Karte finden →"},
        'fr': {'err_country': "Vous avez indiqué un pays entier 🌍. Veuillez préciser une <b>ville</b>.", 'err_city': "Veuillez préciser une ville.", 'not_found': "Aucun hôtel trouvé.", 'found': "options trouvées", 'hotel': "HÔTEL", 'verified': "✓ Vérifié", 'show_prices': "Voir les prix", 'advice_title': "💡 Conseil d'expert pour", 'show_more': "Afficher", 'hotels_more': "hôtels de plus →", 'show_all': "Trouver toutes les options sur la carte →"},
        'it': {'err_country': "Hai indicato un intero paese 🌍. Specifica una <b>città</b>.", 'err_city': "Specifica una città.", 'not_found': "Nessun hotel trovato.", 'found': "opzioni trovate", 'hotel': "HOTEL", 'verified': "✓ Verificato", 'show_prices': "Mostra prezzi", 'advice_title': "💡 Consiglio dell'esperto per", 'show_more': "Mostra altri", 'hotels_more': "hotel →", 'show_all': "Trova tutte le opzioni sulla mappa →"},
        'pt': {'err_country': "Você indicou um país inteiro 🌍. Por favor, especifique uma <b>cidade</b>.", 'err_city': "Por favor, especifique uma cidade.", 'not_found': "Nenhum hotel encontrado.", 'found': "opções encontradas", 'hotel': "HOTEL", 'verified': "✓ Verificado", 'show_prices': "Ver preços", 'advice_title': "💡 Dica de especialista para", 'show_more': "Mostrar mais", 'hotels_more': "hotéis →", 'show_all': "Ver todas as opções no mapa →"},
        'tr': {'err_country': "Bütün bir ülkeyi belirttiniz 🌍. Lütfen bir <b>şehir</b> belirtin.", 'err_city': "Lütfen belirli bir şehir belirtin.", 'not_found': "Otel bulunamadı.", 'found': "seçenek bulundu", 'hotel': "OTEL", 'verified': "✓ Doğrulandı", 'show_prices': "Fiyatları göster", 'advice_title': "💡 Uzman tavsiyesi:", 'show_more': "Daha fazla", 'hotels_more': "otel göster →", 'show_all': "Haritadaki tüm seçenekleri bul →"},
        'zh': {'err_country': "您输入了整个国家 🌍。请指定一个<b>城市</b>。", 'err_city': "请指定一个具体城市。", 'not_found': "未找到酒店。", 'found': "个选项", 'hotel': "酒店", 'verified': "✓ 已验证", 'show_prices': "查看价格", 'advice_title': "💡 专家建议", 'show_more': "显示更多", 'hotels_more': "家酒店 →", 'show_all': "在地图上查找所有选项 →"},
        'ja': {'err_country': "国全体が指定されています🌍。<b>都市</b>を指定してください。", 'err_city': "特定の都市を指定してください。", 'not_found': "ホテルが見つかりません。", 'found': "件のオプション", 'hotel': "ホテル", 'verified': "✓ 確認済み", 'show_prices': "価格を見る", 'advice_title': "💡 専門家のアドバイス", 'show_more': "さらに", 'hotels_more': "件のホテルを表示 →", 'show_all': "地図上ですべてのオプションを見つける →"}
    }
    # Берем словарь для нужного языка. Если язык редкий (например, Хинди), по умолчанию будет Английский
    t = UI_LANGS.get(user_lang, UI_LANGS['en'])

    try:
        p_city = f"Analyze the location in this text: '{msg}'. If it is a COUNTRY, respond ONLY with the word 'COUNTRY'. If it is a CITY, respond ONLY with the city name in English. Nothing else."
        c_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": p_city}]}, timeout=7)
        city_en = c_res.json()['choices'][0]['message']['content'].strip().replace(".", "").lower()
        
        if city_en == "country": return JSONResponse(content={"reply": t['err_country']})
        if not city_en or "none" in city_en or len(city_en) < 2: return JSONResponse(content={"reply": t['err_city']})
        
        intent = "cheap" if any(x in msg for x in ["деш", "low", "бюдж", "cheap", "barato", "billig"]) else "general"
        
        # v14: Кэш теперь разделяется по языкам!
        db_key = f"v14:booking:{city_en}:{intent}:{user_lang}"
        lock_key = f"lock:v14:{city_en}:{intent}:{user_lang}"

        full_list = []
        if redis_db:
            raw = redis_db.get(db_key)
            try:
                parsed = json.loads(raw) if raw else []
                full_list = parsed if isinstance(parsed, list) else []
            except: full_list = []

        if redis_db and not redis_db.get(lock_key):
            existing_ids = [item['id'] for item in full_list if isinstance(item, dict)]
            new_items = get_new_hotels(city_en, intent, existing_ids)

            if new_items:
                # КОМАНДА ДЛЯ НЕЙРОСЕТИ: ПИСАТЬ НА ЯЗЫКЕ ПОЛЬЗОВАТЕЛЯ
                g_prompt = f"""
                I have data about 3 hotels in {city_en}: {json.dumps(new_items)}.
                Your task is to return a valid JSON.
                RULE 1: In 'cats' array, copy hotel data (id, name), and in 'd' write a short description.
                RULE 2: In 'adv' write ONE travel hack for tourists in this city.
                SUPER-RULE: ALL text (descriptions and advice) MUST be written in the language corresponding to ISO code '{user_lang.upper()}'. NO OTHER LANGUAGES ALLOWED!
                JSON ONLY: {{"adv": "text", "cats": [ {{"id": "id", "n": "name", "cat": "{t['hotel']}", "d": "description"}} ]}}
                """
                g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
                    json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": g_prompt}], "response_format": {"type": "json_object"}}, timeout=15)
                
                try:
                    new_data = json.loads(g_res.json()['choices'][0]['message']['content'])
                    last_adv = new_data.get('adv', '')
                    raw_cats = new_data.get('cats', [])
                    
                    if isinstance(raw_cats, dict): raw_cats = [raw_cats]
                    elif isinstance(raw_cats, str):
                        try: raw_cats = json.loads(raw_cats)
                        except: raw_cats = []
                        if isinstance(raw_cats, dict): raw_cats = [raw_cats]

                    for h in raw_cats:
                        if isinstance(h, dict):
                            h['advice'] = last_adv
                            full_list.insert(0, h)
                    
                    if redis_db and full_list:
                        redis_db.set(db_key, json.dumps(full_list))
                        redis_db.set(lock_key, "1", ex=86400)
                except Exception as parse_e: print(f"JSON Parse Error: {parse_e}")

        if not full_list: return JSONResponse(content={"reply": t['not_found']})

        # --- ЛОГИКА ОТОБРАЖЕНИЯ ---
        display_limit = 5
        to_show = full_list[:display_limit]
        hidden_count = len(full_list) - display_limit

        html = f"""
        <div style="font-family: 'BlinkMacSystemFont', sans-serif; width: 100%; color: #1a1a1a; background: transparent; padding: 10px 0; box-sizing: border-box;">
            <div style="max-width: 1000px; margin: 0 auto; box-sizing: border-box;">
                <h2 style="font-size: 20px; font-weight: 700; color: #003580; margin-bottom: 15px; box-sizing: border-box;">{city_en.capitalize()}: {len(full_list)} {t['found']}</h2>
        """
        
        for h in to_show:
            if not isinstance(h, dict): continue
            link = f"https://www.stay22.com/allez/booking/{h.get('id', '')}?aid={STAY22_AID}"
            html += f"""
            <div style="background: #ffffff; border: 1px solid #e7e7e7; border-radius: 8px; padding: 15px; margin-bottom: 12px; display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 15px; box-sizing: border-box;">
                <div style="flex: 1; min-width: 280px; box-sizing: border-box;">
                    <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 6px;">
                        <span style="background: #003580; color: #fff; font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 4px;">{h.get('cat', t['hotel'])}</span>
                        <span style="color: #008009; font-size: 12px; font-weight: 700;">{t['verified']}</span>
                    </div>
                    <div style="font-size: 18px; font-weight: 700; color: #006ce4; margin-bottom: 8px;">{h.get('n', 'Hotel')}</div>
                    <div style="font-size: 13px; color: #4a4a4a; line-height: 1.5;">{h.get('d', '')}</div>
                </div>
                <div style="text-align: right; min-width: 150px; box-sizing: border-box;">
                    <a href="{link}" target="_blank" style="background: #006ce4; color: #ffffff; text-decoration: none; padding: 12px 24px; border-radius: 4px; font-size: 14px; font-weight: 600; display: inline-block; text-align: center; width: 100%; box-sizing: border-box;">{t['show_prices']}</a>
                </div>
            </div>
            """
        
        if len(to_show) > 0 and isinstance(to_show[0], dict) and to_show[0].get('advice'):
            html += f"""
            <div style="background: #ebf3ff; border: 1px solid #003580; border-radius: 8px; padding: 16px; margin: 20px 0; display: flex; align-items: center; gap: 15px; box-sizing: border-box;">
                <div style="background: #003580; color: #fff; border-radius: 50%; min-width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; font-weight: bold;">i</div>
                <div style="font-size: 14px; color: #003580; line-height: 1.5;"><b>{t['advice_title']} {city_en.capitalize()}:</b> {to_show[0]['advice']}</div>
            </div>"""

        all_link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(city_en)}"
        if hidden_count > 0:
            btn_label = f"{t['show_more']} {hidden_count} {t['hotels_more']}"
            btn_style = "background: #ffffff; color: #006ce4; border: 1px solid #006ce4;"
        else:
            btn_label = f"{t['show_all']}"
            btn_style = "background: #003580; color: #ffffff; border: none;"

        html += f"<a href='{all_link}' target='_blank' style='display: block; text-align: center; padding: 16px; text-decoration: none; border-radius: 4px; font-weight: 700; font-size: 15px; box-sizing: border-box; {btn_style}'>{btn_label}</a>"
        
        html += "</div></div>"
        return JSONResponse(content={"reply": html})
    except Exception as e:
        err_msg = "Техническая ошибка:" if user_lang == 'ru' else "Technical error:"
        return JSONResponse(content={"reply": f"{err_msg} {str(e)}"})
