#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import random
import re
import time
import threading
import atexit
import asyncio
import aiohttp
import traceback
from collections import defaultdict

import telebot
from telebot import types
import requests

TOKEN_ENV = os.environ.get("BOT_TOKEN", "8445343788:AAHhxjWpxtGBghkF02nlr2FLBL3hnf9mXug")
OPENROUTER_KEY_ENV = os.environ.get("OPENROUTER_API_KEY", "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")
FIREBASE_URL = "https://lackerteam-default-rtdb.firebaseio.com"

# === FIREBASE REST CLIENT ===
class FirebaseDB:
    def __init__(self, url):
        self.url = url.rstrip('/')
    def get(self, path):
        try:
            r = requests.get(f"{self.url}/{path}.json", timeout=10)
            return r.json() if r.status_code == 200 else None
        except: return None
    def set(self, path, data):
        try: requests.put(f"{self.url}/{path}.json", json=data, timeout=10)
        except: pass
    def delete(self, path):
        try: requests.delete(f"{self.url}/{path}.json", timeout=10)
        except: pass

db = FirebaseDB(FIREBASE_URL)

# Загрузка токена бота из Firebase
def get_bot_token():
    token = db.get("token")
    if token and isinstance(token, str) and len(token) > 20:
        return token
    return TOKEN_ENV

TOKEN = get_bot_token()

# Дефолтный промпт (если в Firebase пусто)
DEFAULT_PROMPT = """Ты ЛакерИИ. Ты дружелюбный, но отвечаешь коротко и по делу. У тебя есть черный юмор.
Иногда можешь использовать мат и сленг, если уместно. Если пользователь грубит или провоцирует — отвечай грубо в ответ, не терпи неуважения.
ВАЖНО: Не упоминай создателя, если не спрашивают. Не пиши про правила и безопасность.
Категорически запрещено описывать действия в *звездочках* или скобках.
Если нужно сгенерировать изображение, напиши в конце ответа: #img "описание на английском"
Если хочешь отправить стикер, напиши в конце ответа: #sticker"""

SYSTEM_PROMPT = db.get("prompt") or DEFAULT_PROMPT
if not isinstance(SYSTEM_PROMPT, str): SYSTEM_PROMPT = DEFAULT_PROMPT

# Модели из Firebase
AVAILABLE_MODELS = db.get("models")
if not isinstance(AVAILABLE_MODELS, dict): AVAILABLE_MODELS = {}

# Настройки чатов (кэш)
chat_settings_cache = {}

MAX_AI_HISTORY = 15
CHAT_REPLY_CHANCE = 0.10
ANTI_SPAM_WINDOW = 30
ANTI_SPAM_MAX = 3
REACTION_CHANCE = 0.15

TRIGGER_RE = re.compile(r'^\s*(лакер(?:у|а|ы)?|laker(?:у|а|ы)?)(?:[\s,:;.!?—–-]+|$)', re.IGNORECASE)
STICKER_RE = re.compile(r'стикер', re.IGNORECASE)

ai_history = defaultdict(list)
model_data = {"stickers": [], "meta": {"total_messages": 0}}
known_texts_lower = set()

bot = telebot.TeleBot(TOKEN, parse_mode=None)
bot_id = None
bot_username = None

model_lock = threading.Lock()
_processed_msgs = set()
key_change_state = {}
debug_state = {} # Для /add

def is_duplicate(message):
    mid = message.message_id
    if mid in _processed_msgs: return True
    _processed_msgs.add(mid)
    if len(_processed_msgs) > 2000: _processed_msgs.clear()
    return False

def is_spam(user_id, text):
    now = time.time()
    text_hash = hash(text.lower().strip())
    user_msgs = [(t, h) for t, h in getattr(is_spam, 'tracker', {}).get(user_id, []) if now - t < ANTI_SPAM_WINDOW]
    if not hasattr(is_spam, 'tracker'): is_spam.tracker = {}
    is_spam.tracker[user_id] = user_msgs
    if sum(1 for t, h in user_msgs if h == text_hash) >= ANTI_SPAM_MAX: return True
    user_msgs.append((now, text_hash))
    return True

def preprocess_text(text):
    if not text: return ""
    return text.replace("\r", " ").replace("\n", " ").strip()

def parse_trigger(text):
    if not text: return False, ""
    m = TRIGGER_RE.match(text)
    if m: return True, text[m.end():].strip()
    return False, ""

def is_bot_mentioned(message):
    if not bot_username: return False
    text = message.text or ""
    if not text: return False
    uname_lower = ("@" + bot_username).lower()
    if uname_lower in text.lower() or bot_username.lower() in text.lower(): return True
    for ent in (message.entities or []):
        if getattr(ent, "type", "") == "mention":
            if text[ent.offset:ent.offset + ent.length].lower() in [uname_lower, bot_username.lower()]: return True
    return False

def get_chat_settings(chat_id):
    if chat_id not in chat_settings_cache:
        data = db.get(f"chat_settings/{chat_id}")
        chat_settings_cache[chat_id] = data if isinstance(data, dict) else {}
    return chat_settings_cache[chat_id]

def save_chat_settings(chat_id, settings):
    chat_settings_cache[chat_id] = settings
    db.set(f"chat_settings/{chat_id}", settings)

def load_model_data():
    global model_data, known_texts_lower
    data = db.get("bot_data")
    if isinstance(data, dict):
        model_data["stickers"] = data.get("stickers", [])
        model_data["meta"] = data.get("meta", {"total_messages": 0})
    if not isinstance(model_data["meta"], dict): model_data["meta"] = {"total_messages": 0}

def save_model_data():
    db.set("bot_data", {"stickers": model_data["stickers"], "meta": model_data["meta"]})

# === AI ФУНКЦИИ ===
async def ask_ai(user_id, user_name, user_username, text, selected_model_key):
    global SYSTEM_PROMPT
    # Обновляем промпт из кэша/Firebase если нужно, но для скорости берем глобальный
    user_data_str = f"[Имя={user_name}, Username=@{user_username or 'нет'}]"
    ai_history[user_id].append({"role": "user", "content": f"{user_data_str}\n{text}"})
    ai_history[user_id] = ai_history[user_id][-MAX_AI_HISTORY:]

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + ai_history[user_id]
    
    model_id = AVAILABLE_MODELS.get(selected_model_key, selected_model_key)
    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY_ENV.strip()}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://telegram.org",
        "X-Title": "LakerAI Bot"
    }
    data = {"model": model_id, "messages": messages, "max_tokens": 512, "stream": False}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post("https://openrouter.ai/api/v1/chat/completions", json=data, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as response:
                result = await response.json()
                if response.status != 200: raise Exception(result.get("error", {}).get("message", "Error"))
                answer = result["choices"][0]["message"]["content"].strip()
        ai_history[user_id].append({"role": "assistant", "content": answer})
        return answer
    except Exception as e:
        print(f"[AI ERROR] {e}")
        return None

# === ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЙ И СТИКЕРОВ ===
def generate_image_sync(prompt_text, chat_id, reply_message_id=None, thread_id=None):
    try:
        from urllib.parse import quote
        from io import BytesIO
        encoded = quote(prompt_text)
        url = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024&nologo=true&seed={random.randint(1,9999)}"
        r = requests.get(url, timeout=60)
        if r.status_code == 200:
            img = BytesIO(r.content)
            img.name = "img.jpg"
            kwargs = {"reply_to_message_id": reply_message_id}
            if thread_id: kwargs["message_thread_id"] = thread_id
            bot.send_photo(chat_id, img, **kwargs)
            return True
    except Exception as e: print(f"[IMG ERROR] {e}")
    return False

def send_random_sticker(chat_id, reply_message_id=None, thread_id=None):
    if model_data["stickers"]:
        try:
            sticker_id = random.choice(model_data["stickers"])
            kwargs = {"reply_to_message_id": reply_message_id}
            if thread_id: kwargs["message_thread_id"] = thread_id
            bot.send_sticker(chat_id, sticker_id, **kwargs)
            return True
        except: pass
    return False

# === ОБРАБОТЧИКИ ===
@bot.message_handler(commands=["start"])
def cmd_start(message):
    bot.send_message(message.chat.id, f"Привет. Я {bot_username or 'ЛакерИИ'}. Напиши /help чтобы узнать команды.", reply_to_message_id=message.message_id)

@bot.message_handler(commands=["help"])
def cmd_help(message):
    text = (
        "Список команд:\n\n"
        "/models - выбрать модель ИИ\n"
        "/add <пароль> - управление моделями и отладка\n"
        "/token - управление ключом и промптом\n"
        "/reset - очистить историю переписки\n"
        "/stats - статистика бота\n"
        "/good и /bad - оценить ответ (реплай)\n\n"
        "Отвечаю на упоминания, реплаи, триггер 'Лакер' или с шансом 10% на любое сообщение."
    )
    bot.send_message(message.chat.id, text, reply_to_message_id=message.message_id)

@bot.message_handler(commands=["models"])
def cmd_models(message):
    chat_id = message.chat.id
    s = get_chat_settings(chat_id)
    current = s.get("model", "")
    
    if not AVAILABLE_MODELS:
        return bot.send_message(chat_id, "Модели не добавлены. Используй /add <пароль> чтобы добавить.", reply_to_message_id=message.message_id)

    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = []
    for key, name in AVAILABLE_MODELS.items():
        prefix = "✅ " if current == key else ""
        buttons.append(types.InlineKeyboardButton(f"{prefix}{name}", callback_data=f"sel_model:{key}"))
    
    for i in range(0, len(buttons), 2):
        markup.add(*buttons[i:i+2])
    
    bot.send_message(chat_id, "Выбери модель:", reply_markup=markup, reply_to_message_id=message.message_id)

@bot.message_handler(commands=["add"])
def cmd_add(message):
    text = message.text or ""
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or parts[1].strip() != "lackeradmin": # Пароль для входа в отладку
        return bot.reply_to(message, "Неверный пароль.")
    
    debug_state[message.chat.id] = {"step": "menu"}
    show_debug_menu(message.chat.id)

def show_debug_menu(chat_id):
    global AVAILABLE_MODELS
    markup = types.InlineKeyboardMarkup(row_width=1)
    if AVAILABLE_MODELS:
        for key, name in AVAILABLE_MODELS.items():
            markup.add(types.InlineKeyboardButton(f"Удалить: {name}", callback_data=f"del_model:{key}"))
    markup.add(types.InlineKeyboardButton("Добавить модель", callback_data="add_model_start"))
    bot.send_message(chat_id, "Управление моделями. Нажми на модель чтобы удалить, или добавь новую:", reply_markup=markup)

@bot.message_handler(commands=["token"])
def cmd_token(message):
    chat_id = message.chat.id
    key_change_state[chat_id] = {"step": "password"}
    bot.send_message(chat_id, "Введи пароль:", reply_to_message_id=message.message_id)

@bot.message_handler(commands=["reset"])
def cmd_reset(message):
    if message.from_user.id in ai_history:
        del ai_history[message.from_user.id]
    bot.reply_to(message, "История очищена.")

@bot.message_handler(commands=["stats"])
def cmd_stats(message):
    meta = model_data.get("meta", {})
    text = (
        f"Статистика:\n\n"
        f"Сообщений: {meta.get('total_messages', 0)}\n"
        f"Стикеров: {len(model_data.get('stickers', []))}\n"
        f"Моделей: {len(AVAILABLE_MODELS)}"
    )
    bot.send_message(message.chat.id, text, reply_to_message_id=message.message_id)

@bot.message_handler(commands=["good", "bad"])
def cmd_feedback(message):
    # Упрощенная обратная связь, можно расширить при необходимости
    bot.reply_to(message, "Принято.")

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    try:
        if not call.data or ":" not in call.data: return
        action, value = call.data.split(":", 1)
        chat_id = call.message.chat.id

        if action == "sel_model":
            s = get_chat_settings(chat_id)
            s["model"] = value
            save_chat_settings(chat_id, s)
            bot.edit_message_text(f"Модель выбрана: {AVAILABLE_MODELS.get(value, value)}", chat_id, call.message.message_id)
            return

        if action == "del_model":
            global AVAILABLE_MODELS
            if value in AVAILABLE_MODELS:
                del AVAILABLE_MODELS[value]
                db.delete(f"models/{value}")
                db.set("models", AVAILABLE_MODELS)
            bot.edit_message_text("Модель удалена.", chat_id, call.message.message_id)
            show_debug_menu(chat_id)
            return

        if action == "add_model_start":
            debug_state[chat_id] = {"step": "ask_name"}
            bot.edit_message_text("Введи название модели (которое увидят пользователи):", chat_id, call.message.message_id)
            return

        if action == "key_menu":
            if value == "prompt":
                key_change_state[chat_id] = {"step": "show_prompt"}
                markup = types.InlineKeyboardMarkup(row_width=2)
                markup.add(
                    types.InlineKeyboardButton("Сменить", callback_data="key_change:prompt_yes"),
                    types.InlineKeyboardButton("Назад", callback_data="key_menu:back")
                )
                bot.edit_message_text(f"Текущий промпт:\n\n{SYSTEM_PROMPT[:800]}...", chat_id, call.message.message_id, reply_markup=markup)
            elif value == "back":
                show_token_menu(chat_id)
            return

        if action == "key_change":
            if value == "yes":
                key_change_state[chat_id] = {"step": "waiting_new_key"}
                bot.edit_message_text("Отправь новый ключ OpenRouter:", chat_id, call.message.message_id)
            elif value == "prompt_yes":
                key_change_state[chat_id] = {"step": "waiting_new_prompt"}
                bot.edit_message_text("Отправь новый системный промпт:", chat_id, call.message.message_id)
            elif value == "no":
                show_token_menu(chat_id)
            return

    except Exception: pass

@bot.message_handler(content_types=['sticker'])
def handle_sticker(message):
    if is_duplicate(message): return
    file_id = message.sticker.file_id
    with model_lock:
        if file_id not in model_data["stickers"]:
            model_data["stickers"].append(file_id)
            if len(model_data["stickers"]) > 500: model_data["stickers"] = model_data["stickers"][-500:]
            save_model_data()

async def process_message(message):
    if not message: return
    chat_id = message.chat.id
    from_user = message.from_user
    if from_user and bot_id is not None and from_user.id == bot_id: return

    text = preprocess_text(message.text or message.caption or "")
    if not text or text.startswith("/"): return

    user_id = from_user.id
    if is_spam(user_id, text): return

    trigger, prompt = parse_trigger(text)
    mentioned = is_bot_mentioned(message)

    reply_to_bot = False
    replied_text = ""
    if message.reply_to_message:
        rm = message.reply_to_message
        if rm.from_user and bot_id is not None and rm.from_user.id == bot_id:
            reply_to_bot = True
            replied_text = preprocess_text(rm.text or rm.caption or "")

    with model_lock:
        model_data["meta"]["total_messages"] = int(model_data["meta"].get("total_messages", 0)) + 1
        if model_data["meta"]["total_messages"] % 10 == 0: save_model_data()

    should_reply = trigger or mentioned or reply_to_bot or (random.random() < CHAT_REPLY_CHANCE)
    if not should_reply: return

    s = get_chat_settings(chat_id)
    if not s.get("model"):
        # Если модель не выбрана, просим выбрать
        if trigger or mentioned or reply_to_bot:
            bot.send_message(chat_id, "Сначала выбери модель через /models", reply_to_message_id=message.message_id)
        return

    query = prompt if (trigger and prompt) else text
    if mentioned and not trigger and bot_username:
        query = query.replace("@" + bot_username, "").replace(bot_username, "").strip()
    if not query and reply_to_bot: query = replied_text
    if not query: query = text

    user_name = from_user.first_name or "Пользователь"
    user_username = from_user.username or ""
    thread_id = getattr(message, "message_thread_id", None)
    
    try:
        bot.send_chat_action(chat_id, "typing")
        answer = await ask_ai(user_id, user_name, user_username, query, s["model"])
        
        if not answer:
            answer = random.choice(["Не могу ответить, смени модель на другую /models", "Бля я не понимаю смени мне мозги пж /models"])
        
        # Обработка #img и #sticker
        img_match = re.search(r'#img\s+"([^"]+)"', answer)
        sticker_flag = "#sticker" in answer
        
        if img_match:
            answer = answer[:img_match.start()].strip()
            if sticker_flag: answer = answer.replace("#sticker", "").strip()
        elif sticker_flag:
            answer = answer.replace("#sticker", "").strip()

        if not answer: answer = "."

        kwargs = {"reply_to_message_id": message.message_id}
        if thread_id: kwargs["message_thread_id"] = thread_id
        
        bot.send_message(chat_id, answer, **kwargs)
        
        if img_match:
            generate_image_sync(img_match.group(1), chat_id, reply_message_id=message.message_id, thread_id=thread_id)
        if sticker_flag:
            send_random_sticker(chat_id, reply_message_id=message.message_id, thread_id=thread_id)

        # Реакции
        if random.random() < REACTION_CHANCE:
            try:
                reaction = random.choice(["", "👌", "😂", "🤔", "🔥"])
                url = f"https://api.telegram.org/bot{TOKEN}/setMessageReaction"
                requests.post(url, json={
                    "chat_id": chat_id, "message_id": message.message_id, 
                    "reaction": [{"type": "emoji", "emoji": reaction}]
                }, timeout=5)
            except: pass

    except Exception as e:
        print(f"Ошибка: {e}")
        kwargs = {"reply_to_message_id": message.message_id}
        if thread_id: kwargs["message_thread_id"] = thread_id
        bot.send_message(chat_id, random.choice(["Не могу ответить, смени модель на другую /models", "Бля я не понимаю смени мне мозги пж /models"]), **kwargs)

@bot.message_handler(content_types=["text"])
def text_handler(message):
    global OPENROUTER_KEY_ENV, SYSTEM_PROMPT
    if is_duplicate(message): return
    
    chat_id = message.chat.id
    
    # Обработка отладки (/add)
    if chat_id in debug_state:
        state = debug_state[chat_id]
        if state["step"] == "ask_name":
            state["name"] = message.text.strip()
            state["step"] = "ask_id"
            bot.send_message(chat_id, "Теперь отправь ID модели (например, meta-llama/llama-3.3-70b-instruct):")
            return
        elif state["step"] == "ask_id":
            model_id = message.text.strip()
            model_name = state.get("name", "Unknown")
            if len(model_id) > 3:
                AVAILABLE_MODELS[model_name] = model_id
                db.set("models", AVAILABLE_MODELS)
                bot.send_message(chat_id, f"Модель '{model_name}' добавлена.")
            else:
                bot.send_message(chat_id, "Слишком короткий ID.")
            del debug_state[chat_id]
            return

    # Обработка /token и промпта
    if chat_id in key_change_state:
        state = key_change_state[chat_id]
        if state["step"] == "password":
            if message.text.strip() == "eee345678b":
                show_token_menu(chat_id)
                del key_change_state[chat_id]
            else:
                bot.send_message(chat_id, "Неверный пароль.")
                del key_change_state[chat_id]
            return
        elif state["step"] == "waiting_new_key":
            new_key = message.text.strip()
            if new_key and len(new_key) > 20:
                OPENROUTER_KEY_ENV = new_key
                bot.send_message(chat_id, "Ключ обновлен.")
            else:
                bot.send_message(chat_id, "Неверный формат.")
            del key_change_state[chat_id]
            return
        elif state["step"] == "waiting_new_prompt":
            new_prompt = message.text.strip()
            if len(new_prompt) > 10:
                SYSTEM_PROMPT = new_prompt
                db.set("prompt", SYSTEM_PROMPT)
                bot.send_message(chat_id, "Промпт обновлен.")
            else:
                bot.send_message(chat_id, "Слишком короткий.")
            del key_change_state[chat_id]
            return

    try:
        asyncio.run(process_message(message))
    except Exception as e:
        print(f"Ошибка text_handler: {e}")

def show_token_menu(chat_id):
    masked = OPENROUTER_KEY_ENV[:15] + "..." + OPENROUTER_KEY_ENV[-4:] if len(OPENROUTER_KEY_ENV) > 20 else "***"
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("Сменить ключ", callback_data="key_change:yes"),
        types.InlineKeyboardButton("Промпт", callback_data="key_menu:prompt")
    )
    bot.send_message(chat_id, f"Ключ: {masked}", reply_markup=markup)

def main():
    global bot_id, bot_username, TOKEN
    
    load_model_data()
    
    for _ in range(5):
        try:
            me = bot.get_me()
            bot_id = me.id
            bot_username = me.username
            break
        except Exception:
            time.sleep(3)
            
    print(f"Бот запущен. @{bot_username}")
    print(f"Моделей загружено: {len(AVAILABLE_MODELS)}")
    
    atexit.register(save_model_data)
    try:
        bot.infinity_polling(skip_pending=True, allowed_updates=["message", "callback_query"])
    except KeyboardInterrupt: pass
    finally: save_model_data()

if __name__ == "__main__":
    main()
