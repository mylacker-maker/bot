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
from collections import defaultdict

import telebot
from telebot import types
import requests

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8445343788:AAHhxjWpxtGBghkF02nlr2FLBL3hnf9mXug")
FIREBASE_URL = "https://lackerteam-default-rtdb.firebaseio.com"

# Ключи ИИ из кода друга
OPENROUTER_API_KEY_FRIEND = "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"
GROQ_API_KEY_FRIEND = "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"

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

def init_firebase():
    if not db.get("key"):
        db.set("key", "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")
    if not db.get("prompt"):
        db.set("prompt", """Ты ЛакерИИ. Ты дружелюбный, но отвечаешь коротко и по делу. У тебя есть черный юмор.
Иногда можешь использовать мат и сленг, если уместно. Если пользователь грубит или провоцирует — отвечай грубо в ответ, не терпи неуважения.
ВАЖНО: Не упоминай создателя, если не спрашивают. Не пиши про правила и безопасность.
Категорически запрещено описывать действия в *звездочках* или скобках.
Если нужно сгенерировать изображение, напиши в конце ответа: #img "описание на английском"
Если хочешь отправить стикер, напиши в конце ответа: #sticker""")
    if not db.get("models"):
        db.set("models", {
            "DeepSeek": "deepseek/deepseek-chat",
            "Llama 3.3": "meta-llama/llama-3.3-70b-instruct",
            "GPT-4o": "openai/gpt-4o",
            "Claude 3.5": "anthropic/claude-3.5-sonnet"
        })

SYSTEM_PROMPT = ""
AVAILABLE_MODELS = {}
chat_settings_cache = {}
model_data = {"stickers": [], "meta": {"total_messages": 0}}

# Хранилище для аиро-моделей
airo_models = defaultdict(lambda: None)

MAX_AI_HISTORY = 15
CHAT_REPLY_CHANCE = 0.10
ANTI_SPAM_WINDOW = 30
ANTI_SPAM_MAX = 3
REACTION_CHANCE = 0.15

TRIGGER_RE = re.compile(r'^\s*(лакер(?:у|а|ы)?|laker(?:у|а|ы)?)(?:[\s,:;.!?—–-]+|$)', re.IGNORECASE)

ai_history = defaultdict(list)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)
bot_id = None
bot_username = None

model_lock = threading.Lock()
_processed_msgs = set()
key_change_state = {}
debug_state = {}

def reload_data():
    global SYSTEM_PROMPT, AVAILABLE_MODELS
    SYSTEM_PROMPT = db.get("prompt")
    if not isinstance(SYSTEM_PROMPT, str):
        SYSTEM_PROMPT = """Ты ЛакерИИ. Отвечай коротко. Черный юмор. Мат если грубят. #img для картинок. #sticker для стикеров."""
    models = db.get("models")
    AVAILABLE_MODELS = models if isinstance(models, dict) else {}

def is_duplicate(message):
    mid = message.message_id
    if mid in _processed_msgs: return True
    _processed_msgs.add(mid)
    if len(_processed_msgs) > 2000: _processed_msgs.clear()
    return False

def is_spam(user_id, text):
    now = time.time()
    text_hash = hash(text.lower().strip())
    if not hasattr(is_spam, 'tracker'): is_spam.tracker = {}
    user_msgs = [(t, h) for t, h in is_spam.tracker.get(user_id, []) if now - t < ANTI_SPAM_WINDOW]
    is_spam.tracker[user_id] = user_msgs
    if sum(1 for t, h in user_msgs if h == text_hash) >= ANTI_SPAM_MAX: return True
    user_msgs.append((now, text_hash))
    return False

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
    global model_data
    data = db.get("bot_data")
    if isinstance(data, dict):
        model_data["stickers"] = data.get("stickers", [])
        model_data["meta"] = data.get("meta", {"total_messages": 0})
    if not isinstance(model_data["meta"], dict): model_data["meta"] = {"total_messages": 0}

def save_model_data():
    db.set("bot_data", {"stickers": model_data["stickers"], "meta": model_data["meta"]})

# === AI ФУНКЦИИ ИЗ КОДА ДРУГА ===
async def fetch_openrouter_friend(session, messages, model="meta-llama/llama-3.3-70b-instruct"):
    data = {
        "model": model,
        "messages": messages,
        "max_tokens": 4096,
        "temperature": 0.9,
        "stream": False
    }
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY_FRIEND}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://telegram.org",
        "X-Title": "LakerAI Bot"
    }
    async with session.post(
        "https://openrouter.ai/api/v1/chat/completions",
        json=data, headers=headers, timeout=aiohttp.ClientTimeout(total=20)
    ) as response:
        if response.status != 200:
            raise Exception()
        result = await response.json()
        return result["choices"][0]["message"]["content"].strip()

async def fetch_groq_friend(session, messages, model="llama-3.3-70b-versatile"):
    data = {
        "model": model,
        "messages": messages,
        "max_tokens": 4096,
        "temperature": 0.9
    }
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY_FRIEND}",
        "Content-Type": "application/json"
    }
    async with session.post(
        "https://api.groq.com/openai/v1/chat/completions",
        json=data, headers=headers, timeout=aiohttp.ClientTimeout(total=20)
    ) as response:
        if response.status != 200:
            raise Exception()
        result = await response.json()
        return result["choices"][0]["message"]["content"].strip()

async def ask_ai_airo(chat_key, user_name, user_username, text):
    """Запрос к ИИ с использованием аиро-моделей"""
    global SYSTEM_PROMPT
    
    user_data_str = f"[Имя={user_name}, Username=@{user_username or 'нет'}]"
    ai_history[chat_key].append({"role": "user", "content": f"{user_data_str}\n{text}"})
    ai_history[chat_key] = ai_history[chat_key][-MAX_AI_HISTORY:]
    
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + ai_history[chat_key]
    selected_model = airo_models.get(chat_key, "auto")
    
    async with aiohttp.ClientSession() as session:
        try:
            if selected_model == "auto":
                try:
                    answer = await fetch_groq_friend(session, messages, "llama-3.3-70b-versatile")
                except Exception:
                    try:
                        answer = await fetch_openrouter_friend(session, messages, "meta-llama/llama-3.3-70b-instruct")
                    except Exception:
                        answer = await fetch_groq_friend(session, messages, "llama-3.1-8b-instant")
            elif selected_model == "openrouter":
                answer = await fetch_openrouter_friend(session, messages, "meta-llama/llama-3.3-70b-instruct")
            elif selected_model == "groq":
                try:
                    answer = await fetch_groq_friend(session, messages, "llama-3.3-70b-versatile")
                except Exception:
                    answer = await fetch_groq_friend(session, messages, "llama-3.1-8b-instant")
            elif selected_model == "deepseek":
                answer = await fetch_openrouter_friend(session, messages, "deepseek/deepseek-chat")
            else:
                answer = await fetch_openrouter_friend(session, messages, "meta-llama/llama-3.3-70b-instruct")
        except Exception:
            answer = "Не могу ответить сейчас"
    
    ai_history[chat_key].append({"role": "assistant", "content": answer})
    ai_history[chat_key] = ai_history[chat_key][-MAX_AI_HISTORY:]
    return answer

# === СТАНДАРТНЫЙ ЗАПРОС К ИИ ===
async def ask_ai(user_id, user_name, user_username, text, selected_model_key):
    global SYSTEM_PROMPT
    user_data_str = f"[Имя={user_name}, Username=@{user_username or 'нет'}]"
    ai_history[user_id].append({"role": "user", "content": f"{user_data_str}\n{text}"})
    ai_history[user_id] = ai_history[user_id][-MAX_AI_HISTORY:]

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + ai_history[user_id]
    model_id = AVAILABLE_MODELS.get(selected_model_key, selected_model_key)
    
    ai_key = db.get("key") or "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"
    headers = {
        "Authorization": f"Bearer {ai_key.strip()}",
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

@bot.message_handler(commands=["start"])
def cmd_start(message):
    bot.send_message(message.chat.id, f"Привет. Я {bot_username or 'ЛакерИИ'}. Напиши /help чтобы узнать команды.", reply_to_message_id=message.message_id)

@bot.message_handler(commands=["help"])
def cmd_help(message):
    text = (
        "Список команд:\n\n"
        "/models - выбрать модель ИИ\n"
        "/token - управление ключом ИИ и промптом\n"
        "/reset - очистить историю переписки\n"
        "/stats - статистика бота\n"
        "/good и /bad - оценить ответ (реплай)\n\n"
        "Отвечаю на упоминания, реплаи, триггер 'Лакер' или с шансом 10% на любое сообщение."
    )
    bot.send_message(message.chat.id, text, reply_to_message_id=message.message_id)

@bot.message_handler(commands=["models"])
def cmd_models(message):
    chat_id = message.chat.id
    reload_data()
    s = get_chat_settings(chat_id)
    current_normal = s.get("model", "")
    current_airo = airo_models.get(str(chat_id), None)
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    # Кнопка Аиро-модели
    airo_prefix = "🚀 " if current_airo else ""
    airo_button = types.InlineKeyboardButton(f"{airo_prefix}Аиро-модели", callback_data="airo_models_menu")
    markup.add(airo_button)
    
    # Разделитель
    markup.add(types.InlineKeyboardButton("──────────────", callback_data="separator"))
    
    # Остальные модели
    if AVAILABLE_MODELS:
        buttons = []
        for display_name in AVAILABLE_MODELS.keys():
            prefix = "✅ " if current_normal == display_name and not current_airo else ""
            buttons.append(types.InlineKeyboardButton(f"{prefix}{display_name}", callback_data=f"sel_model:{display_name}"))
        
        for i in range(0, len(buttons), 2):
            markup.add(*buttons[i:i+2])
    
    # Показываем текущий режим
    mode_text = " Аиро" if current_airo else (f"📦 {current_normal}" if current_normal else "❌ Не выбрана")
    bot.send_message(chat_id, f"Выбери модель (сейчас: {mode_text}):", reply_markup=markup, reply_to_message_id=message.message_id)

@bot.message_handler(commands=["add"])
def cmd_add(message):
    text = message.text or ""
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or parts[1].strip() != "eee345678b":
        return bot.reply_to(message, "Неверный пароль.")
    
    debug_state[message.chat.id] = {"step": "menu"}
    show_debug_menu(message.chat.id)

def show_debug_menu(chat_id):
    reload_data()
    markup = types.InlineKeyboardMarkup(row_width=1)
    if AVAILABLE_MODELS:
        for display_name in AVAILABLE_MODELS.keys():
            markup.add(types.InlineKeyboardButton(f"Удалить: {display_name}", callback_data=f"del_model:{display_name}"))
    markup.add(types.InlineKeyboardButton("Добавить модель", callback_data="add_model_start"))
    bot.send_message(chat_id, "Управление моделями:", reply_markup=markup)

@bot.message_handler(commands=["token"])
def cmd_token(message):
    chat_id = message.chat.id
    key_change_state[chat_id] = {"step": "password"}
    bot.send_message(chat_id, "Введи пароль:", reply_to_message_id=message.message_id)

@bot.message_handler(commands=["reset"])
def cmd_reset(message):
    if message.from_user.id in ai_history:
        del ai_history[message.from_user.id]
    # Сбрасываем обе модели
    chat_id = message.chat.id
    if str(chat_id) in airo_models:
        del airo_models[str(chat_id)]
    s = get_chat_settings(chat_id)
    if "model" in s:
        del s["model"]
        save_chat_settings(chat_id, s)
    bot.reply_to(message, "История и модели очищены.")

@bot.message_handler(commands=["stats"])
def cmd_stats(message):
    meta = model_data.get("meta", {})
    airo_count = sum(1 for v in airo_models.values() if v is not None)
    text = (
        f"Статистика:\n\n"
        f"Сообщений: {meta.get('total_messages', 0)}\n"
        f"Стикеров: {len(model_data.get('stickers', []))}\n"
        f"Моделей в Firebase: {len(AVAILABLE_MODELS)}\n"
        f"Чатов с Аиро: {airo_count}"
    )
    bot.send_message(message.chat.id, text, reply_to_message_id=message.message_id)

@bot.message_handler(commands=["good", "bad"])
def cmd_feedback(message):
    bot.reply_to(message, "Принято.")

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    try:
        if not call.data or (":" not in call.data and call.data not in ["airo_models_menu", "separator", "back_to_models"]):
            bot.answer_callback_query(call.id)
            return
        chat_id = call.message.chat.id

        # Обработка кнопки "Аиро-модели"
        if call.data == "airo_models_menu":
            markup = types.InlineKeyboardMarkup(row_width=1)
            current = airo_models.get(str(chat_id), None)
            
            markup.add(types.InlineKeyboardButton(
                f"{'✅ ' if current == 'auto' else ''}⚡ Авторежим",
                callback_data="airo_model:auto"
            ))
            markup.add(types.InlineKeyboardButton(
                f"{'✅ ' if current == 'openrouter' else ''} OpenRouter",
                callback_data="airo_model:openrouter"
            ))
            markup.add(types.InlineKeyboardButton(
                f"{'✅ ' if current == 'groq' else ''}⚡ Groq",
                callback_data="airo_model:groq"
            ))
            markup.add(types.InlineKeyboardButton(
                f"{'✅ ' if current == 'deepseek' else ''}🧠 DeepSeek",
                callback_data="airo_model:deepseek"
            ))
            markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="back_to_models"))
            
            bot.answer_callback_query(call.id)
            bot.edit_message_text("🚀 Аиро-модели (от друга):\nВыбери движок:", chat_id, call.message.message_id, reply_markup=markup)
            return
        
        # Обработка выбора аиро-модели - СБРАСЫВАЕМ ОБЫЧНУЮ МОДЕЛЬ
        if call.data.startswith("airo_model:"):
            model = call.data.split(":")[1]
            airo_models[str(chat_id)] = model
            
            # Сбрасываем обычную модель
            s = get_chat_settings(chat_id)
            if "model" in s:
                del s["model"]
                save_chat_settings(chat_id, s)
            
            bot.answer_callback_query(call.id, f"Аиро-модель: {model} (обычная сброшена)")
            
            # Показываем меню обратно с обновлением
            reload_data()
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(types.InlineKeyboardButton("🚀 Аиро-модели", callback_data="airo_models_menu"))
            markup.add(types.InlineKeyboardButton("──────────────", callback_data="separator"))
            
            if AVAILABLE_MODELS:
                buttons = []
                for display_name in AVAILABLE_MODELS.keys():
                    # Обычные модели не активны, так как выбрана аиро
                    buttons.append(types.InlineKeyboardButton(f"{display_name}", callback_data=f"sel_model:{display_name}"))
                for i in range(0, len(buttons), 2):
                    markup.add(*buttons[i:i+2])
            
            bot.edit_message_text(f"✅ Выбрана Аиро-модель: {model}\n(Обычная модель сброшена)", chat_id, call.message.message_id, reply_markup=markup)
            return
        
        # Возврат к обычным моделям
        if call.data == "back_to_models":
            reload_data()
            s = get_chat_settings(chat_id)
            current_normal = s.get("model", "")
            current_airo = airo_models.get(str(chat_id), None)
            
            markup = types.InlineKeyboardMarkup(row_width=1)
            airo_prefix = "🚀 " if current_airo else ""
            markup.add(types.InlineKeyboardButton(f"{airo_prefix}Аиро-модели", callback_data="airo_models_menu"))
            markup.add(types.InlineKeyboardButton("──────────────", callback_data="separator"))
            
            if AVAILABLE_MODELS:
                buttons = []
                for display_name in AVAILABLE_MODELS.keys():
                    prefix = "✅ " if current_normal == display_name and not current_airo else ""
                    buttons.append(types.InlineKeyboardButton(f"{prefix}{display_name}", callback_data=f"sel_model:{display_name}"))
                for i in range(0, len(buttons), 2):
                    markup.add(*buttons[i:i+2])
            
            mode_text = "🚀 Аиро" if current_airo else (f"📦 {current_normal}" if current_normal else "❌ Не выбрана")
            bot.answer_callback_query(call.id)
            bot.edit_message_text(f"Выбери модель (сейчас: {mode_text}):", chat_id, call.message.message_id, reply_markup=markup)
            return
        
        # Пропускаем разделитель
        if call.data == "separator":
            bot.answer_callback_query(call.id)
            return
        
        # Обработка обычных моделей - СБРАСЫВАЕМ АИРО МОДЕЛЬ
        if call.data.startswith("sel_model:"):
            _, value = call.data.split(":", 1)
            s = get_chat_settings(chat_id)
            s["model"] = value
            save_chat_settings(chat_id, s)
            
            # Сбрасываем аиро-модель
            if str(chat_id) in airo_models:
                del airo_models[str(chat_id)]
            
            reload_data()
            bot.answer_callback_query(call.id, f"Выбрана: {value} (аиро сброшена)")
            
            # Показываем меню обратно
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(types.InlineKeyboardButton(" Аиро-модели", callback_data="airo_models_menu"))
            markup.add(types.InlineKeyboardButton("──────────────", callback_data="separator"))
            
            if AVAILABLE_MODELS:
                buttons = []
                for display_name in AVAILABLE_MODELS.keys():
                    prefix = "✅ " if value == display_name else ""
                    buttons.append(types.InlineKeyboardButton(f"{prefix}{display_name}", callback_data=f"sel_model:{display_name}"))
                for i in range(0, len(buttons), 2):
                    markup.add(*buttons[i:i+2])
            
            bot.edit_message_text(f"✅ Выбрана модель: {value}\n(Аиро-модель сброшена)", chat_id, call.message.message_id, reply_markup=markup)
            return

        if call.data.startswith("del_model:"):
            _, value = call.data.split(":", 1)
            reload_data()
            if value in AVAILABLE_MODELS:
                del AVAILABLE_MODELS[value]
                db.delete(f"models/{value}")
                db.set("models", AVAILABLE_MODELS)
            bot.answer_callback_query(call.id, "Удалено")
            show_debug_menu(chat_id)
            return

        if call.data == "add_model_start":
            debug_state[chat_id] = {"step": "ask_name"}
            bot.answer_callback_query(call.id)
            bot.edit_message_text("Введи название модели:", chat_id, call.message.message_id)
            return

        if call.data.startswith("key_menu:"):
            _, value = call.data.split(":", 1)
            if value == "prompt":
                key_change_state[chat_id] = {"step": "show_prompt"}
                markup = types.InlineKeyboardMarkup(row_width=2)
                markup.add(
                    types.InlineKeyboardButton("Сменить", callback_data="key_change:prompt_yes"),
                    types.InlineKeyboardButton("Назад", callback_data="key_menu:back")
                )
                bot.answer_callback_query(call.id)
                bot.edit_message_text(f"Текущий промпт:\n\n{SYSTEM_PROMPT[:1000]}", chat_id, call.message.message_id, reply_markup=markup)
            elif value == "back":
                bot.answer_callback_query(call.id)
                show_token_menu(chat_id)
            return

        if call.data.startswith("key_change:"):
            _, value = call.data.split(":", 1)
            if value == "yes":
                key_change_state[chat_id] = {"step": "waiting_new_key"}
                bot.answer_callback_query(call.id)
                bot.edit_message_text("Отправь новый ключ ИИ (OpenRouter):", chat_id, call.message.message_id)
            elif value == "prompt_yes":
                key_change_state[chat_id] = {"step": "waiting_new_prompt"}
                bot.answer_callback_query(call.id)
                bot.edit_message_text("Отправь новый промпт:", chat_id, call.message.message_id)
            elif value == "no":
                bot.answer_callback_query(call.id)
                show_token_menu(chat_id)
            return
            
        bot.answer_callback_query(call.id)
    except Exception as e:
        print(f"[CALLBACK ERROR] {e}")
        try: bot.answer_callback_query(call.id, "Ошибка")
        except: pass

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

    reload_data()
    s = get_chat_settings(chat_id)
    
    # Проверяем, используется ли аиро-модель (приоритет)
    airo_model = airo_models.get(str(chat_id), None)
    
    if airo_model:
        # Используем аиро-модель
        if trigger or mentioned or reply_to_bot:
            user_name = from_user.first_name or "Пользователь"
            user_username = from_user.username or ""
            thread_id = getattr(message, "message_thread_id", None)
            
            try:
                bot.send_chat_action(chat_id, "typing")
                answer = await ask_ai_airo(str(chat_id), user_name, user_username, text)
                
                if not answer:
                    answer = "Не могу ответить сейчас"
                
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
                
                sent_msg = bot.send_message(chat_id, answer, **kwargs)
                
                if img_match:
                    generate_image_sync(img_match.group(1), chat_id, reply_message_id=sent_msg.message_id, thread_id=thread_id)
                if sticker_flag:
                    send_random_sticker(chat_id, reply_message_id=sent_msg.message_id, thread_id=thread_id)

                if random.random() < REACTION_CHANCE:
                    try:
                        reaction = random.choice(["", "👌", "😂", "", ""])
                        url = f"https://api.telegram.org/bot{BOT_TOKEN}/setMessageReaction"
                        requests.post(url, json={
                            "chat_id": chat_id, "message_id": message.message_id, 
                            "reaction": [{"type": "emoji", "emoji": reaction}]
                        }, timeout=5)
                    except: pass
                    
            except Exception as e:
                print(f"Ошибка аиро: {e}")
                kwargs = {"reply_to_message_id": message.message_id}
                if thread_id: kwargs["message_thread_id"] = thread_id
                bot.send_message(chat_id, "Не могу ответить сейчас", **kwargs)
            return
    
    # Обычная логика с моделями из Firebase
    if not s.get("model"):
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
            kwargs = {"reply_to_message_id": message.message_id}
            if thread_id: kwargs["message_thread_id"] = thread_id
            bot.send_message(chat_id, answer, **kwargs)
            return
        
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
        
        sent_msg = bot.send_message(chat_id, answer, **kwargs)
        
        if img_match:
            generate_image_sync(img_match.group(1), chat_id, reply_message_id=sent_msg.message_id, thread_id=thread_id)
        if sticker_flag:
            send_random_sticker(chat_id, reply_message_id=sent_msg.message_id, thread_id=thread_id)

        if random.random() < REACTION_CHANCE:
            try:
                reaction = random.choice(["", "👌", "😂", "", ""])
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/setMessageReaction"
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
    if is_duplicate(message): return
    
    chat_id = message.chat.id
    
    if chat_id in debug_state:
        state = debug_state[chat_id]
        if state["step"] == "ask_name":
            state["name"] = message.text.strip()
            state["step"] = "ask_id"
            bot.send_message(chat_id, "Теперь отправь ID модели:", reply_to_message_id=message.message_id)
            return
        elif state["step"] == "ask_id":
            model_id = message.text.strip()
            model_name = state.get("name", "Unknown")
            if len(model_id) > 3:
                reload_data()
                AVAILABLE_MODELS[model_name] = model_id
                db.set("models", AVAILABLE_MODELS)
                bot.send_message(chat_id, f"Модель '{model_name}' добавлена.", reply_to_message_id=message.message_id)
            else:
                bot.send_message(chat_id, "Слишком короткий ID.", reply_to_message_id=message.message_id)
            del debug_state[chat_id]
            return

    if chat_id in key_change_state:
        state = key_change_state[chat_id]
        if state["step"] == "password":
            if message.text.strip() == "eee345678b":
                show_token_menu(chat_id)
                del key_change_state[chat_id]
            else:
                bot.send_message(chat_id, "Неверный пароль.", reply_to_message_id=message.message_id)
                del key_change_state[chat_id]
            return
        elif state["step"] == "waiting_new_key":
            new_key = message.text.strip()
            if new_key and len(new_key) > 20:
                db.set("key", new_key)
                bot.send_message(chat_id, "Ключ ИИ обновлен.", reply_to_message_id=message.message_id)
            else:
                bot.send_message(chat_id, "Неверный формат.", reply_to_message_id=message.message_id)
            del key_change_state[chat_id]
            return
        elif state["step"] == "waiting_new_prompt":
            new_prompt = message.text.strip()
            if len(new_prompt) > 10:
                db.set("prompt", new_prompt)
                global SYSTEM_PROMPT
                SYSTEM_PROMPT = new_prompt
                bot.send_message(chat_id, "Промпт обновлен.", reply_to_message_id=message.message_id)
            else:
                bot.send_message(chat_id, "Слишком короткий.", reply_to_message_id=message.message_id)
            del key_change_state[chat_id]
            return

    try:
        asyncio.run(process_message(message))
    except Exception as e:
        print(f"Ошибка text_handler: {e}")

def show_token_menu(chat_id):
    ai_key = db.get("key") or "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"
    masked = ai_key[:15] + "..." + ai_key[-4:] if len(ai_key) > 20 else "***"
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("Сменить ключ ИИ", callback_data="key_change:yes"),
        types.InlineKeyboardButton("Промпт", callback_data="key_menu:prompt")
    )
    bot.send_message(chat_id, f"Ключ ИИ (OpenRouter): {masked}", reply_markup=markup)

def main():
    global bot_id, bot_username
    
    init_firebase()
    reload_data()
    load_model_data()
    
    for _ in range(5):
        try:
            me = bot.get_me()
            bot_id = me.id
            bot_username = me.username
            break
        except Exception as e:
            print(f"GetMe error: {e}")
            time.sleep(3)
            
    print(f"Бот запущен. @{bot_username}")
    print(f"Токен: {BOT_TOKEN[:15]}...{BOT_TOKEN[-4:]}")
    print(f"Моделей: {len(AVAILABLE_MODELS)}")
    print(f"Аиро-модели доступны")
    
    atexit.register(save_model_data)
    try:
        bot.infinity_polling(skip_pending=True, allowed_updates=["message", "callback_query"])
    except KeyboardInterrupt: pass
    except Exception as e:
        print(f"Polling error: {e}")
    finally: save_model_data()

if __name__ == "__main__":
    main()
