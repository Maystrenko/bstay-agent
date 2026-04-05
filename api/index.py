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

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

groq_keys = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]
RAPID_API_KEY = os.environ.get("RAPID_API_KEY")
RAPID_HOST = "booking-com18.p.rapidapi.com"
STAY22_AID = "bstay24"

class ChatPayload(BaseModel):
    message: str
    lang: str = "ru"

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    try:
        g_key = random.choice(groq_keys)
        headers = {"Authorization": f"Bearer {g_key}"}

        # 1. Быстро достаем город
        c_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": f"City from: '{payload.message}'. JSON: {{\"c\": \"Name\"}}"}], "response_format": {"type": "json_object"}}, timeout=5)
        city = json.loads(c_res.json()['choices'][0]['message']['content']).get("c", "none")

        if city == "none": return JSONResponse(content={"reply": "Уточните город, пожалуйста."})

        # 2. Ищем 6 лучших отелей (для скорости)
        h_headers = {"X-RapidAPI-Key": RAPID_API_KEY, "X-RapidAPI-Host": RAPID_HOST}
        l_res = requests.get(f"https://{RAPID_HOST}/stays/auto-complete", headers=h_headers, params={"query": city}, timeout=5)
        dest_id = l_res.json()['data'][0]['id']
        
        params = {"locationId": dest_id, "checkinDate": (datetime.now()+timedelta(days=30)).strftime('%Y-%m-%d'), "checkoutDate": (datetime.now()+timedelta(days=33)).strftime('%Y-%m-%d'), "adults": "2", "currency_code": "USD"}
        h_res = requests.get(f"https://{RAPID_HOST}/stays/search", headers=h_headers, params=params, timeout=8)
        hotels_raw = h_res.json().get('data', [])
        if not isinstance(hotels_raw, list): hotels_raw = hotels_raw.get('hotels', []) or hotels_raw.get('results', [])
        
        hotels = [{"id": str(h.get('hotel_id') or h.get('id')), "name": h.get('name') or h.get('hotel_name')} for h in hotels_raw if h.get('id') or h.get('hotel_id')][:6]

        # 3. Генерируем КРАСИВЫЙ гид (но быстро)
        g_prompt = f"Create a short stylish guide for {city} in Russian based on: {json.dumps(hotels)}. Divide into Luxury, Boutique, Budget. 1 sentence per hotel. JSON: {{\"i\": \"intro\", \"cats\": [ {{\"n\": \"cat_name\", \"h\": [ {{\"id\": \"id\", \"n\": \"name\", \"d\": \"desc\"}} ] }} ], \"t\": \"tips\"}}"
        g_res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, 
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": g_prompt}], "response_format": {"type": "json_object"}}, timeout=12)
        g = json.loads(g_res.json()['choices'][0]['message']['content'])

        # 4. Верстка (та самая, красивая)
        html = f"<div style='line-height:1.5;'><p>{g['i']}</p>"
        for cat in g['cats']:
            html += f"<h4 style='color:#003580; margin:15px 0 8px; border-bottom:1px solid #eee;'>{cat['n']}</h4>"
            for h in cat['h']:
                link = f"https://www.stay22.com/allez/booking/{h['id']}?aid={STAY22_AID}"
                html += f"""
                <div style='margin-bottom:10px; padding:10px; background:#f9f9f9; border-radius:8px; border:1px solid #eee; display:flex; justify-content:space-between; align-items:center;'>
                    <div style='padding-right:10px;'><b style='font-size:14px;'>{h['n']}</b><br><small style='color:#666;'>{h['d']}</small></div>
                    <a href='{link}' target='_blank' style='background:#007BFF; color:#fff; text-decoration:none; padding:6px 12px; border-radius:6px; font-weight:bold; font-size:12px;'>Book</a>
                </div>"""
        html += f"<p style='background:#e9f7ef; padding:10px; border-radius:8px; font-size:12px;'><b>Совет:</b> {g['t']}</p></div>"

        return JSONResponse(content={"reply": html})
    except Exception as e:
        return JSONResponse(content={"reply": f"Не удалось составить гид. Попробуйте еще раз."})
