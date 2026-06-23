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

# Крошечная база данных в памяти сервера для хранения истории чатов
# Она будет связывать Telegram ID пользователя с его уникальной комнатой в Dify
user_conversations = {}

# --- Фоновый хелсчек для Render ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Victoria is alive and remembers everything!")
    def log_message(self, format, *args):
        return

def run_health_check_server():
    try:
        port = int(os.getenv("PORT", 8080))
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        server.serve_forever()
    except Exception as e:
        print(f"Ошибка хелсчека: {e}")

# --- Главная функция отправки текста с поддержкой ПАМЯТИ ---
def send_to_dify(text, user_id):
    headers = {
        "Authorization": f"Bearer {DIFY_KEY.strip()}",
        "Content-Type": "application/json"
    }
    base_url = DIFY_URL.strip()
    
    # Достаем сохраненный ID диалога для этого пользователя (если его нет, будет пустая строка)
    active_conversation_id = user_conversations.get(user_id, "")
    
    data = {
        "inputs": {},
        "query": text,
        "response_mode": "streaming",
        "conversation_id": active_conversation_id,  # Передаем старый ID, чтобы продолжить диалог!
        "user": f"telegram_{user_id}"
    }
    
    endpoint = f"{base_url}/chat-messages"
    response = requests.post(endpoint, json=data, headers=headers, stream=True)
    
    if response.status_code not in [200, 201]:
        print(f"Ошибка Dify API: {response.text}")
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
                    
                    # КРИТИЧЕСКИ ВАЖНО: Вытаскиваем ID беседы, который сгенерировал Dify
                    dify_conv_id = event_data.get("conversation_id")
                    if dify_conv_id and not active_conversation_id:
                        # Записываем его в нашу базу данных, чтобы в следующий раз использовать его
                        user_conversations[user_id] = dify_conv_id
                        active_conversation_id = dify_conv_id
                        print(f"Создана новая сессия диалога для {user_id}: {dify_conv_id}")
                        
                    answer_chunk = event_data.get("answer", "")
                    if answer_chunk:
                        full_answer.append(answer_chunk)
                except Exception as parse_err:
                    print(f"Ошибка парсинга чанка: {parse_err}")
                    
    return "".join(full_answer).strip() or "Виктория задумалась. Попробуй еще раз."

# --- Обработчики Telegram ---
@bot.message_handler(commands=['start'])
def start_command(message):
    # При команде /start стираем память конкретно этому пользователю, если нужно начать с чистого листа
    if message.from_user.id in user_conversations:
        del user_conversations[message.from_user.id]
    bot.reply_to(message, "Привет! Я Виктория. Моя память обновлена, теперь я буду помнить всё, о чём мы говорим в рамках нашей беседы!")

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
        
        # На лету конвертируем звук, как в прошлый раз
        import io
        from pydub import AudioSegment
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
        print(f"Голос распознан как: {user_text}")
        
        if not user_text:
            bot.reply_to(message, "Не удалось расслышать слова.")
            return
            
        # Отправляем текст в нашу обновленную функцию с памятью
        answer = send_to_dify(user_text, message.from_user.id)
        bot.reply_to(message, answer)
        
    except Exception as e:
        bot.reply_to(message, f"Ошибка голоса: {e}")

if __name__ == "__main__":
    threading.Thread(target=run_health_check_server, daemon=True).start()
    try: bot.remove_webhook()
    except: pass
    
    print("Бот Виктория запущен в режиме глубокой памяти...")
    bot.infinity_polling()
