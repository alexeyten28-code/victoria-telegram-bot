import os
import telebot
import requests
import json
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

TG_TOKEN = os.getenv("TGBOT_TOKEN")
DIFY_KEY = os.getenv("DIFY_API_KEY")
DIFY_URL = os.getenv("DIFY_API_URL", "https://api.dify.ai/v1")

bot = telebot.TeleBot(TG_TOKEN)

# --- Фоновый хелсчек (оставляем для Render) ---
def run_health_check_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), BaseHTTPRequestHandler)
    server.serve_forever()

# --- Логика отправки ---
def send_voice_to_dify(file_data, user_id):
    headers = {"Authorization": f"Bearer {DIFY_KEY.strip()}"}
    base_url = DIFY_URL.strip()

    # 1. Загружаем файл в хранилище Dify
    files = {'file': ('voice.ogg', file_data, 'audio/ogg')}
    data = {'user': f"telegram_{user_id}"}
    
    upload_resp = requests.post(f"{base_url}/files/upload", headers=headers, files=files, data=data)
    if upload_resp.status_code != 201:
        raise Exception(f"Ошибка загрузки аудио в Dify: {upload_resp.text}")
    
    file_id = upload_resp.json().get("id")

    # 2. Отправляем сообщение в чат с привязкой file_id
    payload = {
        "query": "Расшифруй это аудио и ответь на него.",
        "response_mode": "streaming",
        "user": f"telegram_{user_id}",
        "files": [{
            "type": "audio",
            "transfer_method": "local_file",
            "upload_file_id": file_id
        }]
    }

    response = requests.post(f"{base_url}/chat-messages", json=payload, headers={**headers, "Content-Type": "application/json"}, stream=True)
    
    full_answer = []
    for line in response.iter_lines():
        if line:
            decoded = line.decode('utf-8').strip()
            if decoded.startswith("data:"):
                try:
                    event_data = json.loads(decoded[5:])
                    if event_data.get("answer"):
                        full_answer.append(event_data["answer"])
                except: continue
    return "".join(full_answer)

# --- Обработчик голоса ---
@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    try:
        bot.send_chat_action(message.chat.id, 'typing')
        file_info = bot.get_file(message.voice.file_id)
        file_data = requests.get(f"https://api.telegram.org/file/bot{TG_TOKEN}/{file_info.file_path}").content
        
        answer = send_voice_to_dify(file_data, message.from_user.id)
        bot.reply_to(message, answer or "Виктория прослушала, но не нашла ответа.")
    except Exception as e:
        bot.reply_to(message, f"Ошибка обработки голоса: {str(e)}")

# --- Текстовый обработчик ---
@bot.message_handler(content_types=['text'])
def handle_text(message):
    # Оставляем старую логику для текста (через /chat-messages)
    # ... (логика из предыдущего рабочего варианта)
    pass

if __name__ == "__main__":
    threading.Thread(target=run_health_check_server, daemon=True).start()
    bot.remove_webhook(drop_pending_updates=True)
    bot.skip_pending_updates = True
    bot.infinity_polling()
