import os
import telebot
import requests

TG_TOKEN = os.getenv("TGBOT_TOKEN")
DIFY_KEY = os.getenv("DIFY_API_KEY")
DIFY_URL = os.getenv("DIFY_API_URL", "https://api.dify.ai/v1")

bot = telebot.TeleBot(TG_TOKEN)

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
        
        # 1. Получаем инфо о голосовом файле из Телеграм
        file_info = bot.get_file(message.voice.file_id)
        file_url = f"https://api.telegram.org/file/bot{TG_TOKEN}/{file_info.file_path}"
        
        # 2. Скачиваем аудиофайл
        file_data = requests.get(file_url).content
        
        # 3. Отправляем в Dify Audio-to-Text на расшифровку (Whisper)
        headers = {"Authorization": f"Bearer {DIFY_KEY}"}
        files = {'file': ('voice.ogg', file_data, 'audio/ogg')}
        
        stt_response = requests.post(f"{DIFY_URL}/audio/to-text", headers=headers, files=files)
        stt_response.raise_for_status()
        user_text = stt_response.json().get("text", "")
        
        if not user_text:
            bot.reply_to(message, "Не удалось разобрать голос. Попробуй сказать четче.")
            return
            
        # 4. Полученный текст отправляем Виктории
        answer = send_to_dify(user_text, message.from_user.id)
        bot.reply_to(message, answer)
        
    except Exception as e:
        bot.reply_to(message, f"Ошибка при обработке голоса: {e}")

if __name__ == "__main__":
    print("Бот успешно запущен и слушает команды...")
    bot.infinity_polling()
