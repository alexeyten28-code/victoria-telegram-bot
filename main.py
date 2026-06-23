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
    try:
        port = int(os.getenv("PORT", 8080))
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        print(f"Фоновый хелсчек-сервер успешно запущен на порту {port}")
        server.serve_forever()
    except Exception as e:
        print(f"Ошибка при запуске фонового хелсчек-сервера: {e}")

# --- Работа с API Dify в режиме стриминга ---
def send_to_dify(text, user_id):
    headers = {
        "Authorization": f"Bearer {DIFY_KEY.strip()}",
        "Content-Type": "application/json"
    }
    base_url = DIFY_URL.strip()
    
    data = {
        "inputs": {},
        "query": text,
        "response_mode": "streaming",  # Агенты Dify поддерживают только этот режим
        "conversation_id": "",
        "user": f"telegram_{user_id}"
    }
    
    endpoint = f"{base_url}/chat-messages"
    print(f"Отправка запроса на: {endpoint}")
    
    response = requests.post(endpoint, json=data, headers=headers, stream=True)
    
    if response.status_code != 200 and response.status_code != 201:
        print(f"Ошибка Dify API. Код ответа: {response.status_code}")
        print(f"Тело ответа от Dify: {response.text}")
        response.raise_for_status()
        
    full_answer = []
    
    for line in response.iter_lines():
        if line:
            decoded_line = line.decode('utf-8').strip()
            if decoded_line.startswith("data:"):
                try:
                    json_str = decoded_line[5:].strip()
                    if not json_str:
                        continue
                        
                    event_data = json.loads(json_str)
                    
                    if event_data.get("event") == "error":
                        error_msg = event_data.get("message", "Неизвестная ошибка внутри стрима")
                        raise Exception(f"Dify Stream Error: {error_msg}")
                    
                    answer_chunk = event_data.get("answer", "")
                    if answer_chunk:
                        full_answer.append(answer_chunk)
                        
                except Exception as parse_err:
                    print(f"Предупреждение при чтении чанка стрима: {parse_err}")
                    
    final_text = "".join(full_answer).strip()
    if not final_text:
        return "Извини, Виктория задумалась и не смогла сформулировать ответ. Попробуй еще раз."
        
    return final_text

# --- Обработчики Telegram ---
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
        error_msg = f"Произошла ошибка: {e}"
        if hasattr(e, 'response') and e.response is not None:
            try:
                err_json = e.response.json()
                error_msg += f"\n\nКод ошибки: {err_json.get('code')}\nОписание: {err_json.get('message')}"
            except Exception:
                error_msg += f"\n\nДетали: {e.response.text}"
        bot.reply_to(message, error_msg)

@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    try:
        bot.send_chat_action(message.chat.id, 'typing')
        
        file_info = bot.get_file(message.voice.file_id)
        file_url = f"https://api.telegram.org/file/bot{TG_TOKEN}/{file_info.file_path}"
        
        file_data = requests.get(file_url).content
        
        headers = {"Authorization": f"Bearer {DIFY_KEY.strip()}"}
        base_url = DIFY_URL.strip()
        files = {'file': ('voice.ogg', file_data, 'audio/ogg')}
        
        endpoint = f"{base_url}/audio/to-text"
        print(f"Отправка аудио на расшифровку: {endpoint}")
        
        stt_response = requests.post(endpoint, headers=headers, files=files)
        
        if stt_response.status_code != 200:
            print(f"Ошибка STT API. Код ответа: {stt_response.status_code}")
            print(f"Тело ответа STT от Dify: {stt_response.text}")
            stt_response.raise_for_status()
            
        user_text = stt_response.json().get("text", "")
        
        if not user_text:
            bot.reply_to(message, "Не удалось разобрать голос. Попробуй сказать/написать четче.")
            return
            
        answer = send_to_dify(user_text, message.from_user.id)
        bot.reply_to(message, answer)
        
    except Exception as e:
        error_msg = f"Ошибка при обработке голоса: {e}"
        if hasattr(e, 'response') and e.response is not None:
            try:
                err_json = e.response.json()
                error_msg += f"\n\nКод ошибки: {err_json.get('code')}\nОписание: {err_json.get('message')}"
            except Exception:
                error_msg += f"\n\nДетали: {e.response.text}"
        bot.reply_to(message, error_msg)

if __name__ == "__main__":
    # Запускаем веб-заглушку в отдельном потоке
    threading.Thread(target=run_health_check_server, daemon=True).start()
    
    try:
        print("Сброс старых подключений Telegram...")
        bot.remove_webhook(drop_pending_updates=True)
        time.sleep(1)
    except Exception as ex:
        print(f"Предупреждение при сбросе вебхука: {ex}")
    
    # Включаем пропуск старых обновлений через свойство класса (совместимо со всеми версиями)
    bot.skip_pending_updates = True
    
    print("Бот успешно запущен и слушает команды...")
    bot.infinity_polling()
