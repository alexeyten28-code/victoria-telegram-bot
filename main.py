import os
import telebot
import requests
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

TG_TOKEN = os.getenv("TGBOT_TOKEN")
DIFY_KEY = os.getenv("DIFY_API_KEY")
DIFY_URL = os.getenv("DIFY_API_URL", "https://api.dify.ai/v1")

bot = telebot.TeleBot(TG_TOKEN)

# --- Заглушка для прохождения проверки портов Render ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Victoria is alive!")

    def log_message(self, format, *args):
        return  # Отключаем лишние логи в консоли Render

def run_health_check_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    print(f"Фоновый хелсчек-сервер запущен на порту {port}")
    server.serve_forever()
# --------------------------------------------------------

def send_to_dify(text, user_id):
    headers = {
        "Authorization": f"Bearer {DIFY_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "inputs": {},
        "query": text,
        "response_mode": "blocking",
        "user": f"tg_{user_id}"
    }
    response = requests.post(f"{DIFY_URL}/chat-messages", json=data, headers=headers)
    response.raise_for_status()
    return response.json().get("answer", "Извини, не удалось получить ответ от Виктории.")

@bot.message_handler(commands=['start'])
def start_command(message):
    bot.reply_to(message, "Привет! Я Виктория, твой личный ассистент. Готова к работе! Напиши мне или наговори голосовое сообщение.")

@bot.message_handler(content_types=['text'])
def handle_text(message):
    try:
        bot.send_chat_action(message.chat.id, 'typing')
        answer = send_to_dify(message.text, message.from_user.id)
        bot.reply_to(message, answer)
    except Exception as e:
        bot.reply_to(message, f"Произошла ошибка: {e}")

@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    try:
        bot.send_chat_action(message.chat.id, 'typing')
        
        file_info = bot.get_file(message.voice.file_id)
        file_url = f"https://api.telegram.org/file/bot{TG_TOKEN}/{file_info.file_path}"
        
        file_data = requests.get(file_url).content
        
        headers = {"Authorization": f"Bearer {DIFY_KEY}"}
        files = {'file': ('voice.ogg', file_data, 'audio/ogg')}
        
        stt_response = requests.post(f"{DIFY_URL}/audio/to-text", headers=headers, files=files)
        stt_response.raise_for_status()
        user_text = stt_response.json().get("text", "")
        
        if not user_text:
            bot.reply_to(message, "Не удалось разобрать голос. Попробуй сказать четче.")
            return
            
        answer = send_to_dify(user_text, message.from_user.id)
        bot.reply_to(message, answer)
        
    except Exception as e:
        bot.reply_to(message, f"Ошибка при обработке голоса: {e}")

if __name__ == "__main__":
    # Запускаем веб-заглушку в отдельном потоке, чтобы она не мешала боту
    threading.Thread(target=run_health_check_server, daemon=True).start()
    
    print("Бот успешно запущен и слушает команды...")
    bot.infinity_polling()
