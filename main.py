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

bot = telebot.TeleBot(TG_TOKEN)

# --- Фоновый хелсчек для прохождения проверок портов Render ---
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

# --- Главная функция отправки текста Виктории (Streaming режим) ---
def send_to_dify(text, user_id):
    headers = {
        "Authorization": f"Bearer {DIFY_KEY.strip()}",
        "Content-Type": "application/json"
    }
    base_url = DIFY_URL.strip()
    
    data = {
        "inputs": {},
        "query": text,
        "response_mode": "streaming",  # Агенты Dify работают только в streaming
        "conversation_id": "",
        "user": f"telegram_{user_id}"
    }
    
    endpoint = f"{base_url}/chat-messages"
    response = requests.post(endpoint, json=data, headers=headers, stream=True)
    
    if response.status_code not in [200, 201]:
        print(f"Ошибка Dify Chat API. Код: {response.status_code}, Ответ: {response.text}")
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
                        
                    answer_chunk = event_data.get("answer", "")
                    if answer_chunk:
                        full_answer.append(answer_chunk)
                except Exception as parse_err:
                    print(f"Ошибка парсинга чанка: {parse_err}")
                    
    return "".join(full_answer).strip() or "Виктория приняла запрос, но сформировала пустой ответ."

# --- Обработчики Telegram ---
@bot.message_handler(commands=['start'])
def start_command(message):
    bot.reply_to(message, "Привет! Я Виктория, твой личный ассистент. Теперь я полноценно понимаю и текст, и голосовые сообщения!")

@bot.message_handler(content_types=['text'])
def handle_text(message):
    try:
        bot.send_chat_action(message.chat.id, 'typing')
        answer = send_to_dify(message.text, message.from_user.id)
        bot.reply_to(message, answer)
    except Exception as e:
        bot.reply_to(message, f"Ошибка при обработке текста: {e}")

@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    try:
        bot.send_chat_action(message.chat.id, 'typing')
        
        # 1. Скачиваем голосовое сообщение (.ogg) из серверов Telegram
        file_info = bot.get_file(message.voice.file_id)
        file_url = f"https://api.telegram.org/file/bot{TG_TOKEN}/{file_info.file_path}"
        ogg_data = requests.get(file_url).content
        
        print("Конвертируем Telegram OGG в совместимый WAV формат...")
        # 2. На лету с помощью pydub пересобираем аудио в чистый WAV, который Dify обожает
        ogg_stream = io.BytesIO(ogg_data)
        audio = AudioSegment.from_file(ogg_stream, format="ogg")
        
        wav_stream = io.BytesIO()
        audio.export(wav_stream, format="wav")
        wav_data = wav_stream.getvalue()
        
        headers = {"Authorization": f"Bearer {DIFY_KEY.strip()}"}
        base_url = DIFY_URL.strip()
        
        # Передаем уже сконвертированный WAV файл в multipart/form-data
        files = {'file': ('voice.wav', wav_data, 'audio/wav')}
        data = {'user': f"telegram_{message.from_user.id}"}
        
        endpoint = f"{base_url}/audio-to-text"
        print(f"Отправка WAV аудио на расшифровку по адресу: {endpoint}")
        
        stt_response = requests.post(endpoint, headers=headers, files=files, data=data)
        
        if stt_response.status_code != 200:
            print(f"Ошибка перевода в текст. Код: {stt_response.status_code}, Текст: {stt_response.text}")
            stt_response.raise_for_status()
            
        # Забираем распознанный текст сообщения
        user_text = stt_response.json().get("text", "")
        print(f"Голос успешно распознан как: {user_text}")
        
        if not user_text:
            bot.reply_to(message, "Я прослушала аудио, но не смогла расслышать слова. Попробуй сказать четче.")
            return
            
        # Отправляем полученный текст Агенту Виктории
        answer = send_to_dify(user_text, message.from_user.id)
        bot.reply_to(message, answer)
        
    except Exception as e:
        error_msg = f"Ошибка при обработке голоса: {e}"
        if hasattr(e, 'response') and e.response is not None:
            error_msg += f"\n\nДетали от Dify: {e.response.text}"
        bot.reply_to(message, error_msg)

if __name__ == "__main__":
    # Фоновое простукивание портов для стабильности Render
    threading.Thread(target=run_health_check_server, daemon=True).start()
    
    # Базовый безопасный сброс вебхуков
    try:
        print("Сброс старых сессий Telegram...")
        bot.remove_webhook()
        time.sleep(1)
    except Exception: 
        pass
    
    print("Бот Виктория успешно запущен и ожидает сообщений...")
    bot.infinity_polling(timeout=20, long_polling_timeout=10)
