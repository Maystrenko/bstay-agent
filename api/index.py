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
    lang: str = "en" 

def get_new_hotels(city_en, existing_ids):
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
    user_lang = payload.lang.lower()[:2]
    
    g_key = random.choice(groq_keys)
    headers = {"Authorization": f"Bearer {g_key}"}

    UI_LANGS = {
        'ru': {'err_city': "Пожалуйста, укажите конкретный город.", 'not_found': "По вашему запросу отели не найдены.", 'found': "вариантов найдено", 'hotel': "ОТЕЛЬ", 'verified': "✓ Проверено", 'show_prices': "Показать цены", 'advice_title': "💡 Совет эксперта по", 'show_more': "Показать ещё", 'hotels_more': "вариантов из нашей базы ⬇️", 'show_all': "🗺️ Посмотреть все варианты на карте", 'more_cmd': "Еще", 'filter_msg': "Отфильтровано по вашему запросу!", 'found_hotel': "🎯 Вот отель, который вы искали:"},
        'en': {'err_city': "Please specify a specific city.", 'not_found': "No hotels found matching your request.", 'found': "options found", 'hotel': "HOTEL", 'verified': "✓ Verified", 'show_prices': "Show prices", 'advice_title': "💡 Expert advice for", 'show_more': "Show", 'hotels_more': "more from our database ⬇️", 'show_all': "🗺️ Find all options on map", 'more_cmd': "More", 'filter_msg': "Filtered matching your request!", 'found_hotel': "🎯 Here is the hotel you were looking for:"},
        'es': {'err_city': "Por favor, especifica una ciudad.", 'not_found': "No se encontraron hoteles.", 'found': "opciones encontradas", 'hotel': "HOTEL", 'verified': "✓ Verificado", 'show_prices': "Ver precios", 'advice_title': "💡 Consejo para", 'show_more': "Mostrar", 'hotels_more': "más de nuestra base ⬇️", 'show_all': "🗺️ Ver todas las opciones en el mapa", 'more_cmd': "Más", 'filter_msg': "¡Filtrado según su solicitud!", 'found_hotel': "🎯 Aquí está el hotel que buscaba:"},
        'de': {'err_city': "Bitte geben Sie eine Stadt an.", 'not_found': "Keine Hotels gefunden.", 'found': "Optionen gefunden", 'hotel': "HOTEL", 'verified': "✓ Überprüft", 'show_prices': "Preise anzeigen", 'advice_title': "💡 Tipp für", 'show_more': "Zeige", 'hotels_more': "weitere aus unserer Datenbank ⬇️", 'show_all': "🗺️ Alle Optionen auf der Karte finden", 'more_cmd': "Mehr", 'filter_msg': "Gefiltert nach Ihrer Anfrage!", 'found_hotel': "🎯 Hier ist das gesuchte Hotel:"},
        'fr': {'err_city': "Veuillez préciser une ville.", 'not_found': "Aucun hôtel trouvé.", 'found': "options trouvées", 'hotel': "HÔTEL", 'verified': "✓ Vérifié", 'show_prices': "Voir les prix", 'advice_title': "💡 Conseil pour", 'show_more': "Afficher", 'hotels_more': "plus de notre base ⬇️", 'show_all': "🗺️ Trouver toutes les options sur la carte", 'more_cmd': "Plus", 'filter_msg': "Filtré selon votre demande !", 'found_hotel': "🎯 Voici l'hôtel que vous cherchiez :"}
    }
    t = UI_LANGS.get(user_lang, UI_LANGS['en'])

    try:
        # ШАГ 1: АНАЛИЗАТОР ТЕПЕРЬ ИЩЕТ И ГОРОДА, И КОНКРЕТНЫЕ ОТЕЛИ
        analyzer_prompt = f"""
        Analyze the user's travel query: '{msg}'. Respond ONLY with valid JSON.
        {{
          "city": "Specific city name in English (if country, put capital city. If none, null)",
          "hotel": "Specific hotel name if requested (e.g. 'Rixos', 'Hilton', 'The Savoy'), else null",
          "filter": "Translate specific request to English (e.g. 'pool', 'cheap', 'center'). If no specific request, null",
          "wants_more": true if user asks for 'more', 'next', 'еще', 'другие', else false
        }}
        """
        c_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
            json={"model": "llama-3.1-8b-instant", "messages": [{"role": "user", "content": analyzer_prompt}], "response_format": {"type": "json_object"}}, timeout=5)
        
        try: intent_data = json.loads(c_res.json()['choices'][0]['message']['content'])
        except: return JSONResponse(content={"reply": t['err_city']})

        city_en = intent_data.get('city')
        hotel_name = intent_data.get('hotel')
        user_filter = intent_data.get('filter')
        wants_more = intent_data.get('wants_more', False)

        # --- ЗАПАСНОЙ ПАРАШЮТ: ПОИСК КОНКРЕТНОГО ОТЕЛЯ ---
        if hotel_name:
            query_str = f"{hotel_name} {city_en}" if city_en else hotel_name
            try:
                headers_rap = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": "booking-com18.p.rapidapi.com"}
                l_res = requests.get("https://booking-com18.p.rapidapi.com/stays/auto-complete", headers=headers_rap, params={"query": query_str}, timeout=10)
                results = l_res.json().get('data', [])
                
                target_hotel = None
                for r in results:
                    if r.get('dest_type') == 'hotel': # Ищем именно тип "Отель"
                        target_hotel = r
                        break
                if not target_hotel and results: target_hotel = results[0]
                    
                if target_hotel:
                    h_id = str(target_hotel.get('id') or target_hotel.get('hotel_id', ''))
                    if h_id:
                        h_name = target_hotel.get('name', hotel_name.title())
                        # ИИ мгновенно пишет описание для найденного отеля
                        g_prompt = f"Write 1 short sentence describing the hotel '{h_name}' in language ISO '{user_lang.upper()}'. JSON: {{\"d\": \"text\"}}"
                        try:
                            g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
                                json={"model": "llama-3.1-8b-instant", "messages": [{"role": "user", "content": g_prompt}], "response_format": {"type": "json_object"}}, timeout=5)
                            h_desc = json.loads(g_res.json()['choices'][0]['message']['content']).get('d', '')
                        except: h_desc = h_name
                            
                        link = f"https://www.stay22.com/allez/booking/{h_id}?aid={STAY22_AID}"
                        
                        html = f"""
                        <div style="font-family: 'BlinkMacSystemFont', sans-serif; width: 100%; color: #1a1a1a; background: transparent; padding: 10px 0; box-sizing: border-box;">
                            <div style="max-width: 1000px; margin: 0 auto; box-sizing: border-box;">
                                <h2 style="font-size: 20px; font-weight: 700; color: #003580; margin-bottom: 15px; box-sizing: border-box;">{t['found_hotel']}</h2>
                                <div style="background: #ffffff; border: 1px solid #e7e7e7; border-radius: 8px; padding: 15px; margin-bottom: 12px; display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 15px; box-sizing: border-box;">
                                    <div style="flex: 1; min-width: 280px; box-sizing: border-box;">
                                        <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 6px;">
                                            <span style="background: #003580; color: #fff; font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 4px;">{t['hotel']}</span>
                                            <span style="color: #008009; font-size: 12px; font-weight: 700;">{t['verified']}</span>
                                        </div>
                                        <div style="font-size: 18px; font-weight: 700; color: #006ce4; margin-bottom: 8px;">{h_name}</div>
                                        <div style="font-size: 13px; color: #4a4a4a; line-height: 1.5;">{h_desc}</div>
                                    </div>
                                    <div style="text-align: right; min-width: 150px; box-sizing: border-box;">
                                        <a href="{link}" target="_blank" style="background: #006ce4; color: #ffffff; text-decoration: none; padding: 12px 24px; border-radius: 4px; font-size: 14px; font-weight: 600; display: inline-block; text-align: center; width: 100%; box-sizing: border-box;">{t['show_prices']}</a>
                                    </div>
                                </div>
                            </div>
                        </div>
                        """
                        return JSONResponse(content={"reply": html})
            except Exception as e: print(f"Hotel fallback error: {e}")
        # --- КОНЕЦ БЛОКА ---

        # ЕСЛИ ЭТО ОБЫЧНЫЙ ПОИСК ГОРОДА, ИДЕМ ПО СТАРОМУ ПУТИ:
        if not city_en: return JSONResponse(content={"reply": t['err_city']})
        city_en = city_en.lower()
        
        db_key = f"v16:booking:{city_en}:{user_lang}"
        lock_key = f"lock:v16:{city_en}:{user_lang}"

        full_list = []
        if redis_db:
            raw = redis_db.get(db_key)
            try:
                parsed = json.loads(raw) if raw else []
                full_list = parsed if isinstance(parsed, list) else []
            except: full_list = []

        if redis_db and not redis_db.get(lock_key):
            existing_ids = [item['id'] for item in full_list if isinstance(item, dict)]
            new_items = get_new_hotels(city_en, existing_ids)

            if new_items:
                g_prompt = f"""
                I have data about 3 hotels in {city_en}: {json.dumps(new_items)}.
                Your task is to return a valid JSON.
                RULE 1: In 'cats' array, copy hotel data (id, name), and in 'd' write a short description.
                RULE 2: In 'adv' write ONE travel hack for tourists in this city.
                SUPER-RULE: ALL text MUST be written in the language corresponding to ISO code '{user_lang.upper()}'. NO OTHER LANGUAGES ALLOWED!
                JSON ONLY: {{"adv": "text", "cats": [ {{"id": "id", "n": "name", "cat": "{t['hotel']}", "d": "description"}} ]}}
                """
                g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
                    json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": g_prompt}], "response_format": {"type": "json_object"}}, timeout=15)
                
                try:
                    new_data = json.loads(g_res.json()['choices'][0]['message']['content'])
                    last_adv = new_data.get('adv', '')
                    raw_cats = new_data.get('cats', [])
                    if isinstance(raw_cats, dict): raw_cats = [raw_cats]
                    for h in raw_cats:
                        if isinstance(h, dict):
                            h['advice'] = last_adv
                            full_list.insert(0, h)
                    
                    if redis_db and full_list:
                        redis_db.set(db_key, json.dumps(full_list))
                        redis_db.set(lock_key, "1", ex=86400)
                except: pass

        if not full_list: return JSONResponse(content={"reply": t['not_found']})

        to_show = []
        is_filtered = False

        if user_filter and len(full_list) > 3:
            f_prompt = f"Find up to 5 hotels matching: '{user_filter}'. Hotels: {json.dumps([{'id': h.get('id'), 'n': h.get('n'), 'd': h.get('d')} for h in full_list[:30]])}. Respond ONLY with JSON: {{\"ids\": [\"id1\", \"id2\"]}}"
            f_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
                json={"model": "llama-3.1-8b-instant", "messages": [{"role": "user", "content": f_prompt}], "response_format": {"type": "json_object"}}, timeout=5)
            try:
                filtered_ids = json.loads(f_res.json()['choices'][0]['message']['content']).get('ids', [])
                to_show = [h for h in full_list if h.get('id') in filtered_ids][:5]
                is_filtered = True
            except: to_show = full_list[:5]
        elif wants_more and len(full_list) > 5:
            to_show = random.sample(full_list, min(5, len(full_list)))
        
        if not to_show:
            to_show = full_list[:5]

        hidden_count = len(full_list) - len(to_show)

        subtitle = f"<span style='color: #008009; font-size: 14px;'>✨ {t['filter_msg']}</span>" if is_filtered else f"{len(full_list)} {t['found']}"
        html = f"""
        <div style="font-family: 'BlinkMacSystemFont', sans-serif; width: 100%; color: #1a1a1a; background: transparent; padding: 10px 0; box-sizing: border-box;">
            <div style="max-width: 1000px; margin: 0 auto; box-sizing: border-box;">
                <h2 style="font-size: 20px; font-weight: 700; color: #003580; margin-bottom: 15px; box-sizing: border-box;">{city_en.capitalize()}: {subtitle}</h2>
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
        
        if len(to_show) > 0 and isinstance(to_show[0], dict) and to_show[0].get('advice') and not is_filtered:
            html += f"""
            <div style="background: #ebf3ff; border: 1px solid #003580; border-radius: 8px; padding: 16px; margin: 20px 0; display: flex; align-items: center; gap: 15px; box-sizing: border-box;">
                <div style="background: #003580; color: #fff; border-radius: 50%; min-width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; font-weight: bold;">i</div>
                <div style="font-size: 14px; color: #003580; line-height: 1.5;"><b>{t['advice_title']} {city_en.capitalize()}:</b> {to_show[0]['advice']}</div>
            </div>"""

        all_link = f"https://www.stay22.com/allez/{STAY22_AID}?address={urllib.parse.quote(city_en)}"
        
        if hidden_count > 0:
            btn_label = f"{t['show_more']} {hidden_count} {t['hotels_more']}"
            btn_action = f"let inp=document.getElementById('user-input'); if(inp){{inp.value='{t['more_cmd']} {city_en.capitalize()}'; document.getElementById('ui-button').click();}} return false;"
            html += f"<button onclick=\"{btn_action}\" style='display: block; width: 100%; text-align: center; padding: 16px; margin-bottom: 12px; background: #ffffff; color: #006ce4; border: 1px solid #006ce4; border-radius: 8px; font-weight: 700; font-size: 15px; cursor: pointer; box-sizing: border-box;'>{btn_label}</button>"

        html += f"<a href='{all_link}' target='_blank' style='display: block; width: 100%; text-align: center; padding: 16px; text-decoration: none; border-radius: 8px; font-weight: 700; font-size: 15px; box-sizing: border-box; background: #003580; color: #ffffff; border: none;'>{t['show_all']}</a>"
        
        html += "</div></div>"
        return JSONResponse(content={"reply": html})
    except Exception as e:
        err_msg = "Техническая ошибка:" if user_lang == 'ru' else "Technical error:"
        return JSONResponse(content={"reply": f"{err_msg} {str(e)}"})
