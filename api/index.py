import os
import json
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse # Добавили новый импорт
from pydantic import BaseModel
import google.generativeai as genai

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") 
STAY22_AID = "bstay24"

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash')

class ChatPayload(BaseModel):
    user_id: str
    message: str
    chat_history: list

@app.post("/api/chat")
async def handle_chat(payload: ChatPayload):
    # Добавляем в промпт требование игнорировать историю полностью
    extract_prompt = f"Extract the city name from: '{payload.message}'. ONLY the city name in English. No context, no history. If no city, say 'none'."
    
    try:
        response = model.generate_content(extract_prompt)
        new_city = response.text.strip().split('\n')[0].replace(".", "").replace("City:", "").strip()
        
        if "none" in new_city.lower() or len(new_city) < 2:
            return JSONResponse(
                content={"reply": "Назовите город, и я найду лучшие варианты!"},
                headers={"Cache-Control": "no-store, no-cache, must-revalidate"}
            )
            
        booking_url = f"https://www.booking.com/searchresults.html?ss={new_city}"
        stay22_link = f"https://www.stay22.com/allez/{STAY22_AID}?campaign=ai-bot&link={booking_url}"
        
        answer_prompt = f"""
        User wants {new_city}. Write 2 sentences in user's language. 
        Add this button:
        <br><br><a href='{stay22_link}' target='_blank' style='display:inline-block; padding:12px 24px; background:#007BFF; color:white; text-decoration:none; border-radius:8px; font-weight:bold;'>Посмотреть отели в {new_city}</a>
        Return ONLY HTML.
        """
        
        final_res = model.generate_content(answer_prompt)
        clean_html = final_res.text.replace("```html", "").replace("```", "").strip()
        
        # Возвращаем ответ с ЗАПРЕТОМ на кеширование
        return JSONResponse(
            content={"reply": clean_html},
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, proxy-revalidate",
                "Pragma": "no-cache",
                "Expires": "0"
            }
        )
        
    except Exception as e:
        return {"reply": f"Ошибка: {str(e)}"}
