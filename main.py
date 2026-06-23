import os
import telebot
import requests
import threading
import json
import time
import io
from pydub import AudioSegment
from http.server import BaseHTTPRequestHandler, HTTPServer

TG_TOKEN = os.getenv("TGBOT_TOKEN")
DIFY_KEY = os.getenv("DIFY_API_KEY")
DIFY_URL = os.getenv("DIFY_API_URL", "https://api.dify.ai/v1")
ADMIN_TG_ID = os.getenv("ADMIN_TG_ID")

bot = telebot.TeleBot(TG_TOKEN)
user_conversations = {}

def trigger_proactive_reminder(event_title, user_id):
    try:
        system_query = f"Системный хук: Напомни мне в своем фирменном дружелюбном стиле (с эмодзи и скобочками), что через 5 минут начнется созвон/встреча: «{event_title}»."
        victoria_style_answer = send_to_dify(system_query, user_id)
        bot.send_message(user_id, victoria_style_answer)
    except Exception as e:
        print(f"Ошибка отправки напоминания: {e}")

# --- Облегченный веб-сервер для хелсчеков и пингов ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Отдаем микроскопический ответ "OK", чтобы cron-job.org не выдавал ошибку "Output too large"
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")
        
    def do_POST(self):
        if self.path == '/webhook-reminder':
            try:
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                data = json.loads(post_data.decode('utf-8'))
                event_title = data.get("title", "Важная встреча")
                
                if ADMIN_TG_ID:
                    threading.Thread(
                        target=trigger_proactive_reminder, 
                        args=(event_title, int(ADMIN_TG_ID)), 
                        daemon=True
                    ).start()
                
                self.send_response(200)
                self.send_header("Content-type", "text/plain")
                self.end_headers()
                self.wfile.write(b"OK")
            except Exception as e:
                print(f"Ошибка POST: {e}")
                self.send_response(500)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        return  # Отключаем лишний спам в логах Render

def run_health_check_server():
    try:
        port = int(os.getenv("PORT", 8080))
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        server.serve_forever()
    except Exception as e:
        print(f"Ошибка хелсчека: {e}")

# --- Работа с Dify (Стриминг + Память) ---
def send_to_dify(text, user_id):
    headers = {
        "Authorization": f"Bearer {DIFY_KEY.strip()}",
        "Content-Type": "application/json"
    }
    base_url = DIFY_URL.strip()
    active_conversation_id = user_conversations.get(user_id, "")
    
    data = {
        "inputs": {},
        "query": text,
        "response_mode": "streaming",
        "conversation_id": active_conversation_id,
        "user": f"telegram_{user_id}"
    }
    
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
                        raise Exception(event_data.get("message", "Ошибка стрима"))
                    
                    dify_conv_id = event_data.get("conversation_id")
                    if dify_conv_id and not active_conversation_id:
                        user_conversations[user_id] = dify_conv_id
                        active_conversation_id = dify_conv_id
                        
                    answer_chunk = event_data.get("answer", "")
                    if answer_chunk:
                        full_answer.append(answer_chunk)
                except Exception as parse_err:
                    print(f"Ворнинг чанка: {parse_err}")
                    
    return "".join(full_answer).strip() or "Виктория задумалась. Попробуй еще раз."

# --- Обработчики Telegram ---
@bot.message_handler(commands=['start'])
def start_command(message):
    if message.from_user.id in user_conversations:
        del user_conversations[message.from_user.id]
    bot.reply_to(message, "Привет! Я Виктория. Моя память обновлена, я готова к работе! Напиши мне или пришли голосовое )")

@bot.message_handler(content_types=['text'])
def handle_text(message):
    try:
        bot.send_chat_action(message.chat.id, 'typing')
        answer = send_to_dify(message.text, message.from_user.id)
        bot.reply_to(message, answer)
    except Exception as e:
        bot.reply_to(message, f"Ошибка текста: {e}")

@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    try:
        bot.send_chat_action(message.chat.id, 'typing')
        
        file_info = bot.get_file(message.voice.file_id)
        file_url = f"https://api.telegram.org/file/bot{TG_TOKEN}/{file_info.file_path}"
        ogg_data = requests.get(file_url).content
        
        ogg_stream = io.BytesIO(ogg_data)
        audio = AudioSegment.from_file(ogg_stream, format="ogg")
        wav_stream = io.BytesIO()
        audio.export(wav_stream, format="wav")
        wav_data = wav_stream.getvalue()
        
        headers = {"Authorization": f"Bearer {DIFY_KEY.strip()}"}
        base_url = DIFY_URL.strip()
        files = {'file': ('voice.wav', wav_data, 'audio/wav')}
        data = {'user': f"telegram_{message.from_user.id}"}
        
        stt_response = requests.post(f"{base_url}/audio-to-text", headers=headers, files=files, data=data)
        if stt_response.status_code != 200:
            stt_response.raise_for_status()
            
        user_text = stt_response.json().get("text", "")
        if not user_text:
            bot.reply_to(message, "Не удалось расслышать слова.")
            return
            
        answer = send_to_dify(user_text, message.from_user.id)
        bot.reply_to(message, answer)
    except Exception as e:
        bot.reply_to(message, f"Ошибка голоса: {e}")

# --- Точка входа с правильным разделением потоков ---
if __name__ == "__main__":
    # 1. Запускаем веб-сервер в ФОНОВОМ потоке, чтобы он не блокировал бота
    flask_thread = threading.Thread(target=run_health_check_server)
    flask_thread.daemon = True
    flask_thread.start()
    
    try: bot.remove_webhook()
    except: pass
    
    # 2. В основном потоке запускаем бессмертный цикл опроса Telegram
    while True:
        try:
            print("Бот успешно запущен и слушает Telegram...")
            bot.infinity_polling(timeout=20, long_polling_timeout=10)
        except Exception as crash_error:
            print(f"Сбой сети Telegram: {crash_error}. Перезапуск через 5 секунд...")
            time.sleep(5)
