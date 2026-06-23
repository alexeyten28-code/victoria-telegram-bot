import os
import telebot
import requests
import threading
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

TG_TOKEN = os.getenv("TGBOT_TOKEN")
DIFY_KEY = os.getenv("DIFY_API_KEY")
DIFY_URL = os.getenv("DIFY_API_URL", "https://api.dify.ai/v1")

bot = telebot.TeleBot(TG_TOKEN)

# --- Фоновый хелсчек ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Victoria is alive!")
    def log_message(self, format, *args):
        return

def run_health_check_server():
    try:
        port = int(os.getenv("PORT", 8080))
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        server.serve_forever()
    except Exception as e:
        print(f"Ошибка хелсчека: {e}")

# --- Загрузка файлов в Dify (Картинки / Аудио) ---
def upload_file_to_dify(file_data, file_name, mime_type, user_id):
    headers = {"Authorization": f"Bearer {DIFY_KEY.strip()}"}
    base_url = DIFY_URL.strip()
    
    files = {'file': (file_name, file_data, mime_type)}
    data = {'user': f"telegram_{user_id}"}
    
    endpoint = f"{base_url}/files/upload"
    print(f"Загрузка файла в Dify: {endpoint}")
    
    resp = requests.post(endpoint, headers=headers, files=files, data=data)
    if resp.status_code != 201 and resp.status_code != 200:
        print(f"Ошибка загрузки файла в Dify: {resp.text}")
        resp.raise_for_status()
        
    return resp.json().get("id")

# --- Главная функция отправки запроса в Dify ---
def send_to_dify(text, user_id, dify_file_id=None, file_type=None):
    headers = {
        "Authorization": f"Bearer {DIFY_KEY.strip()}",
        "Content-Type": "application/json"
    }
    base_url = DIFY_URL.strip()
    
    data = {
        "inputs": {},
        "query": text if text else "Посмотри на этот файл",
        "response_mode": "streaming",
        "conversation_id": "",
        "user": f"telegram_{user_id}",
        "files": []
    }
    
    # Если передан файл (картинка или голос), прикрепляем его к запросу к Агенту
    if dify_file_id:
        data["files"].append({
            "type": file_type,  # 'image' или 'audio'
            "transfer_method": "local_file",
            "upload_file_id": dify_file_id
        })
    
    endpoint = f"{base_url}/chat-messages"
    response = requests.post(endpoint, json=data, headers=headers, stream=True)
    
    if response.status_code not in [200, 201]:
        response.raise_for_status()
        
    full_answer = []
    for line in response.iter_lines():
        if line:
            decoded_line = line.decode('utf-8').strip()
            if decoded_line.startswith("data:"):
                try:
                    json_str = decoded_line[5:].strip()
                    if not json_str: continue
                    event_data = json.loads(json_str)
                    
                    if event_data.get("event") == "error":
                        raise Exception(event_data.get("message", "Ошибка стрима Dify"))
                        
                    answer_chunk = event_data.get("answer", "")
                    if answer_chunk:
                        full_answer.append(answer_chunk)
                except Exception as parse_err:
                    print(f"Чнк-ворнинг: {parse_err}")
                    
    return "".join(full_answer).strip() or "Виктория не смогла ответить. Попробуйте еще раз."

# --- Обработчики Telegram ---
@bot.message_handler(commands=['start'])
def start_command(message):
    bot.reply_to(message, "Привет! Я Виктория. Теперь я умею не только читать текст, но и понимать голосовые, а также распознавать документы и картинки! Отправляй.")

@bot.message_handler(content_types=['text'])
def handle_text(message):
    try:
        bot.send_chat_action(message.chat.id, 'typing')
        answer = send_to_dify(message.text, message.from_user.id)
        bot.reply_to(message, answer)
    except Exception as e:
        bot.reply_to(message, f"Ошибка: {e}")

# НОВЫЙ ОБРАБОТЧИК КАРТИНОК
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    try:
        bot.send_chat_action(message.chat.id, 'typing')
        
        # Берем лучшее качество фото
        photo = message.photo[-1]
        file_info = bot.get_file(photo.file_id)
        file_url = f"https://api.telegram.org/file/bot{TG_TOKEN}/{file_info.file_path}"
        file_data = requests.get(file_url).content
        
        # 1. Загружаем картинку в Dify
        dify_file_id = upload_file_to_dify(file_data, "image.jpg", "image/jpeg", message.from_user.id)
        
        # 2. Отправляем в чат-ассистент текст подписи к фото (если есть) или стандартную просьбу
        caption = message.caption if message.caption else "Переведи этот текст с картинки в текстовый формат и изучи его."
        
        answer = send_to_dify(caption, message.from_user.id, dify_file_id=dify_file_id, file_type="image")
        bot.reply_to(message, answer)
        
    except Exception as e:
        bot.reply_to(message, f"Ошибка при обработке изображения: {e}")

# ИСПРАВЛЕННЫЙ ОБРАБОТЧИК ГОЛОСА
@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    try:
        bot.send_chat_action(message.chat.id, 'typing')
        
        file_info = bot.get_file(message.voice.file_id)
        file_url = f"https://api.telegram.org/file/bot{TG_TOKEN}/{file_info.file_path}"
        file_data = requests.get(file_url).content
        
        # 1. Отправляем аудиофайл в Dify как обычный ресурс (чтобы Агент сам его слушал)
        dify_file_id = upload_file_to_dify(file_data, "voice.ogg", "audio/ogg", message.from_user.id)
        
        # 2. Передаем ассистенту аудиофайл на расшифровку и ответ
        answer = send_to_dify("Слушай голосовое сообщение", message.from_user.id, dify_file_id=dify_file_id, file_type="audio")
        bot.reply_to(message, answer)
        
    except Exception as e:
        bot.reply_to(message, f"Ошибка при обработке голоса: {e}")

if __name__ == "__main__":
    threading.Thread(target=run_health_check_server, daemon=True).start()
    try:
        bot.remove_webhook(drop_pending_updates=True)
        time.sleep(1)
    except Exception: pass
    
    bot.skip_pending_updates = True
    print("Бот Виктория обновлен и готов к тестам картинок и голоса...")
    bot.infinity_polling()
