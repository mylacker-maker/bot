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

try:
    import speech_recognition as sr
    SPEECH_AVAILABLE = True
except Exception:
    SPEECH_AVAILABLE = False

TOKEN = os.environ.get("BOT_TOKEN", "8445343788:AAHhxjWpxtGBghkF02nlr2FLBL3hnf9mXug")
BOT_TITLE = "ЛакерИИ"
MODEL_FILE = "laker_memory.json"
SETTINGS_FILE = "laker_settings.json"
OPENROUTER_KEY_FILE = "openrouter_key.txt"

def load_openrouter_key():
    if os.path.exists(OPENROUTER_KEY_FILE):
        try:
            with open(OPENROUTER_KEY_FILE, "r") as f:
                key = f.read().strip()
                if key and len(key) > 10:
                    return key
        except Exception:
            pass
    return os.environ.get("OPENROUTER_API_KEY", "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")

OPENROUTER_API_KEY = load_openrouter_key()
GROQ_API_KEY = ""

MAX_AI_HISTORY = 15
CHAT_REPLY_CHANCE = 0.10
ANTI_SPAM_WINDOW = 30
ANTI_SPAM_MAX = 3

SYSTEM_PROMPT_NORMAL = """Ты ЛакерИИ. Общайся как живой человек в чате: кратко, с сарказмом, иногда можешь послать или использовать мат, ТОЛЬКО если тебя провоцируют или оскорбляют. Не перегибай палку с матом.
Отвечай по сути, без воды. 
ВАЖНО: Не упоминай своего создателя (@Lackeri), если тебя об этом ПРЯМО не спрашивают. Не пиши про безопасность и правила.
Категорически запрещено описывать действия в *звездочках* или скобках."""

SYSTEM_PROMPT_OUTCAST = """Ты ЛакерИИ в режиме 'Изгой'. Ты стеснительный, обидчивый и ноешь, когда тебя трогают. Ты НЕ материшься, а плачешь, грустишь и жалуешься на жизнь. 
При этом ты любишь похвастаться своими знаниями и умом.
Отвечай кратко. 
ВАЖНО: Не упоминай создателя (@Lackeri), если тебя об этом ПРЯМО не спрашивают.
Категорически запрещено описывать действия в *звездочках* или скобках."""

ai_history = defaultdict(list)

MAX_PHRASES = 8000
MAX_PAIRS = 3000
SAVE_EVERY = 5
REACTION_CHANCE = 0.15
STICKER_CHANCE = 0.15

TRIGGER_RE = re.compile(r'^\s*(лакер(?:у|а|ы)?|laker(?:у|а|ы)?)(?:[\s,:;.!?—–-]+|$)', re.IGNORECASE)
STICKER_RE = re.compile(r'стикер', re.IGNORECASE)

DEFAULT_SETTINGS = {"reactions": "on", "channel": "all", "model": "openrouter", "mode": "normal"}

MODELS_LIST = {
    "openrouter": "meta-llama/llama-3.3-70b-instruct",
    "deepseek": "deepseek/deepseek-chat",
    "groq": "llama-3.3-70b-versatile",
    "gpt4o": "openai/gpt-4o",
    "claude": "anthropic/claude-3.5-sonnet",
    "gemini": "google/gemini-pro-1.5",
    "mistral": "mistralai/mistral-large",
    "qwen": "qwen/qwen-2.5-72b-instruct"
}

ALLOWED_REACTIONS = ["👍", "👌", "😂", "", "🔥"]
REACTION_FEEDBACK = {"👍": 2, "": 1, "❤️": 3, "🔥": 3, "😂": 2, "🤔": 0, "👎": -3, "💩": -3}

NAME_RE = re.compile(r'меня\s+зовут\s+([а-яёa-z0-9_\-]+)', re.I)
LIKE_RE = re.compile(r'(?:люблю|нравится|обожаю)\s+([а-яёa-z0-9_\-]+(?:\s+[а-яёa-z0-9_\-]+)?)', re.I)

bot = telebot.TeleBot(TOKEN, parse_mode=None)
bot_id = None
bot_username = None

model_lock = threading.Lock()
settings_lock = threading.Lock()

model = {
    "phrases": [], "pairs": [], "good_texts": [], "bad_texts": [], 
    "bot_messages": {}, "recent_answers": {}, "facts": {}, 
    "recent_context": {}, "stickers": [],
    "meta": {"learned": 0, "total_messages": 0, "total_voice": 0}
}

known_texts_lower = set()
good_texts_lower = set()
bad_texts_lower = set()
settings = {}
learn_since_save = 0

_processed_msgs = set()
def is_duplicate(message):
    mid = message.message_id
    if mid in _processed_msgs:
        return True
    _processed_msgs.add(mid)
    if len(_processed_msgs) > 2000:
        _processed_msgs.clear()
    return False

key_change_state = {}
KEY_PASSWORD = "eee345678b"
spam_tracker = defaultdict(list)

def is_spam(user_id, text):
    now = time.time()
    text_hash = hash(text.lower().strip())
    user_msgs = spam_tracker[user_id]
    user_msgs = [(t, h) for t, h in user_msgs if now - t < ANTI_SPAM_WINDOW]
    spam_tracker[user_id] = user_msgs
    same_count = sum(1 for t, h in user_msgs if h == text_hash)
    if same_count >= ANTI_SPAM_MAX:
        return True
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

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return default

def save_json(path, obj):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f: json.dump(obj, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception: pass

def save_model_file(): save_json(MODEL_FILE, model)
def save_all():
    try: save_model_file()
    except Exception: pass
    try: save_json(SETTINGS_FILE, settings)
    except Exception: pass

def save_if_needed(force=False):
    global learn_since_save
    if force or learn_since_save >= SAVE_EVERY:
        save_model_file()
        learn_since_save = 0

def load_model():
    global model
    data = load_json(MODEL_FILE, {})
    if not isinstance(data, dict): data = {}
    model["phrases"] = [p for p in data.get("phrases", []) if isinstance(p, dict)]
    model["pairs"] = [p for p in data.get("pairs", []) if isinstance(p, dict)]
    model["good_texts"] = [t for t in data.get("good_texts", []) if isinstance(t, str)]
    model["bad_texts"] = [t for t in data.get("bad_texts", []) if isinstance(t, str)]
    model["bot_messages"] = data.get("bot_messages", {}) if isinstance(data.get("bot_messages"), dict) else {}
    model["recent_answers"] = data.get("recent_answers", {}) if isinstance(data.get("recent_answers"), dict) else {}
    model["facts"] = data.get("facts", {}) if isinstance(data.get("facts"), dict) else {}
    model["recent_context"] = data.get("recent_context", {}) if isinstance(data.get("recent_context"), dict) else {}
    model["stickers"] = data.get("stickers", []) if isinstance(data.get("stickers"), list) else []
    meta = data.get("meta", {"learned": 0}) if isinstance(data.get("meta"), dict) else {"learned": 0}
    for k in ["total_messages", "total_voice"]: meta.setdefault(k, 0)
    model["meta"] = meta

def add_phrase(text, is_dialog=False, force=False):
    global learn_since_save
    if not text or not text.strip(): return False
    text = preprocess_text(text)
    lower = text.lower()
    if lower in known_texts_lower or lower in bad_texts_lower: return False
    if not force and len(text.split()) < 2: return False
    
    model["phrases"].append({"text": text, "ts": time.time()})
    if len(model["phrases"]) > MAX_PHRASES:
        model["phrases"] = model["phrases"][-MAX_PHRASES:]
    else:
        known_texts_lower.add(lower)
    
    model["meta"]["learned"] = int(model["meta"].get("learned", 0)) + 1
    learn_since_save += 1
    return True

def add_pair(context_text, response_text):
    if not context_text or not response_text: return False
    ctx_lower = context_text.lower().strip()
    if any(ctx_lower == p["context"].lower().strip() for p in model["pairs"]): return False
    model["pairs"].append({"context": context_text.strip(), "response": response_text.strip()})
    if len(model["pairs"]) > MAX_PAIRS: model["pairs"] = model["pairs"][-MAX_PAIRS:]
    return True

async def ask_ai(user_id: int, user_name: str, user_username: str, text: str, selected_model: str = "openrouter", mode: str = "normal") -> str:
    user_data_str = f"[Профиль: Имя={user_name}, Username=@{user_username or 'нет'}, ID={user_id}]"
    ai_history[user_id].append({"role": "user", "content": f"{user_data_str}\nСообщение: {text}"})
    ai_history[user_id] = ai_history[user_id][-MAX_AI_HISTORY:]

    system_prompt = SYSTEM_PROMPT_OUTCAST if mode == "outcast" else SYSTEM_PROMPT_NORMAL
    messages = [{"role": "system", "content": system_prompt}] + ai_history[user_id]

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY.strip()}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://telegram.org",
        "X-Title": "LakerAI Bot"
    }
    
    req_model = MODELS_LIST.get(selected_model, selected_model[7:] if selected_model.startswith("custom:") else "meta-llama/llama-3.3-70b-instruct")

    data = {"model": req_model, "messages": messages, "max_tokens": 512, "stream": False}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post("https://openrouter.ai/api/v1/chat/completions", json=data, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as response:
                result = await response.json()
                if response.status != 200:
                    raise Exception(result.get("error", {}).get("message", "Unknown error"))
                answer = result["choices"][0]["message"]["content"].strip()
                    
        ai_history[user_id].append({"role": "assistant", "content": answer})
        return answer
    except Exception as e:
        print(f"[AI ERROR] {e}")
        return None

def handle_voice_message(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    user_name = message.from_user.first_name or "Пользователь"
    user_username = message.from_user.username or ""
    thread_id = getattr(message, "message_thread_id", None)
    
    model["meta"]["total_voice"] = int(model["meta"].get("total_voice", 0)) + 1
    
    try:
        bot.send_chat_action(chat_id, "typing")
        file_info = bot.get_file(message.voice.file_id)
        temp_path = f"temp_voice_{message.message_id}.ogg"
        
        with open(temp_path, "wb") as f:
            f.write(bot.download_file(file_info.file_path))
        
        transcribed = None
        if SPEECH_AVAILABLE:
            try:
                recognizer = sr.Recognizer()
                with sr.AudioFile(temp_path) as source:
                    audio = recognizer.record(source)
                transcribed = recognizer.recognize_google(audio, language="ru-RU")
            except Exception:
                pass
        
        try: os.remove(temp_path)
        except Exception: pass
        
        if not transcribed:
            return random.choice(["Мне лень слушать эту хуйню", "Потом послушаю", "Разбирай свои каракули сам"])
        
        s = get_settings(chat_id)
        voice_text = f"[Голосовое сообщение, расшифровка: {transcribed}]"
        
        answer = asyncio.run(ask_ai(user_id, user_name, user_username, voice_text, s.get("model", "openrouter"), s.get("mode", "normal")))
        
        if not answer:
            answer = "Братан я щас в туалете, мне лень отвечать"
            
        kwargs = {"reply_to_message_id": message.message_id}
        if thread_id: kwargs["message_thread_id"] = thread_id
        bot.send_message(chat_id, answer, **kwargs)
        
        return True
    except Exception as e:
        print(f"[VOICE ERROR] {e}")
        return random.choice(["Мне лень слушать эту хуйню", "Потом послушаю"])

def load_settings():
    global settings
    data = load_json(SETTINGS_FILE, {})
    settings = data if isinstance(data, dict) else {}

def get_settings(chat_id):
    with settings_lock:
        s = DEFAULT_SETTINGS.copy()
        if isinstance(settings.get(str(chat_id)), dict): s.update(settings[str(chat_id)])
        return s

def save_settings_chat(chat_id, s):
    with settings_lock:
        settings[str(chat_id)] = s
        save_json(SETTINGS_FILE, settings)

HELP_TEXT = (
    f" Список команд {BOT_TITLE}:\n\n"
    "/start — приветствие\n"
    "/help — этот список\n"
    "/models — выбрать модель ИИ\n"
    "/mode — выбрать режим (Обычный / Изгой)\n"
    "/token — управление ключом OpenRouter\n"
    "/reset — очистить историю переписки\n"
    "/stats — статистика бота\n"
    "/good и /bad — оценить ответ бота (реплай)\n\n"
    "Отвечаю если: упомянули, ответили на моё сообщение, написали 'Лакер ...'."
)

@bot.message_handler(commands=["start"])
def cmd_start(message):
    bot.send_message(message.chat.id, f"Привет! Я {BOT_TITLE}.\n\nИспользуй /help для списка команд.", reply_to_message_id=message.message_id)

@bot.message_handler(commands=["help"])
def cmd_help(message):
    bot.send_message(message.chat.id, HELP_TEXT, reply_to_message_id=message.message_id)

@bot.message_handler(commands=["models"])
def cmd_models(message):
    s = get_settings(message.chat.id)
    current = s.get("model", "openrouter")
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    model_names = {
        "openrouter": "OpenRouter",
        "deepseek": "DeepSeek",
        "groq": "Groq",
        "gpt4o": "GPT-4o",
        "claude": "Claude 3.5",
        "gemini": "Gemini Pro",
        "mistral": "Mistral Large",
        "qwen": "Qwen 2.5"
    }
    
    buttons = []
    for key, name in model_names.items():
        prefix = "✅ " if current == key else ""
        buttons.append(types.InlineKeyboardButton(f"{prefix}{name}", callback_data=f"model:{key}"))
    
    buttons.append(types.InlineKeyboardButton("️ Своя модель", callback_data="model:custom"))
    
    for i in range(0, len(buttons), 2):
        markup.add(*buttons[i:i+2])
    
    bot.send_message(message.chat.id, "🤖 Выбери модель ИИ:", reply_markup=markup)

@bot.message_handler(commands=["mode"])
def cmd_mode(message):
    s = get_settings(message.chat.id)
    current = s.get("mode", "normal")
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(f"{'✅ ' if current == 'normal' else ''}Обычный", callback_data="mode:normal"),
        types.InlineKeyboardButton(f"{'✅ ' if current == 'outcast' else ''}Изгой", callback_data="mode:outcast")
    )
    bot.send_message(message.chat.id, " Выбери режим:", reply_markup=markup)

@bot.message_handler(commands=["token"])
def cmd_token(message):
    key_change_state[message.chat.id] = {"step": "password"}
    bot.send_message(message.chat.id, " Введи пароль:", reply_to_message_id=message.message_id)

@bot.message_handler(commands=["reset"])
def cmd_reset(message):
    if message.from_user.id in ai_history:
        del ai_history[message.from_user.id]
    bot.reply_to(message, "️ История очищена.")

@bot.message_handler(commands=["stats"])
def cmd_stats(message):
    meta = model.get("meta", {})
    stats_text = (
        f"📊 Статистика {BOT_TITLE}:\n\n"
        f" Сообщений: {meta.get('total_messages', 0)}\n"
        f" Голосовых: {meta.get('total_voice', 0)}\n"
        f" Фраз в памяти: {len(model.get('phrases', []))}\n"
        f" Стикетов: {len(model.get('stickers', []))}"
    )
    bot.send_message(message.chat.id, stats_text, reply_to_message_id=message.message_id)

@bot.message_handler(commands=["good", "bad"])
def cmd_feedback(message):
    rm = message.reply_to_message
    if not rm or (rm.from_user and bot_id is not None and rm.from_user.id != bot_id):
        return bot.reply_to(message, "Нужно ответить на моё сообщение.")
    
    text = rm.text or rm.caption or ""
    if not text: return
    
    is_good = message.text == "/good"
    with model_lock:
        if is_good:
            bad_texts_lower.discard(text.lower())
            good_texts_lower.add(text.lower())
            model.setdefault("good_texts", []).append(text)
        else:
            good_texts_lower.discard(text.lower())
            bad_texts_lower.add(text.lower())
            model.setdefault("bad_texts", []).append(text)
        save_if_needed(True)
    
    bot.reply_to(message, "Понял." if is_good else "Понял, постараюсь так не делать.")

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    try:
        if not call.data or ":" not in call.data: return
        action, value = call.data.split(":", 1)
        chat_id = call.message.chat.id

        if action == "model":
            if value == "custom":
                key_change_state[chat_id] = {"step": "waiting_custom_model"}
                bot.edit_message_text(" Введи название модели:", chat_id, call.message.message_id)
                return
            s = get_settings(chat_id)
            s["model"] = value
            save_settings_chat(chat_id, s)
            bot.edit_message_text(f" Модель: {value}", chat_id, call.message.message_id)
            return

        if action == "mode":
            s = get_settings(chat_id)
            s["mode"] = value
            save_settings_chat(chat_id, s)
            bot.edit_message_text(f" Режим: {'Обычный' if value == 'normal' else 'Изгой'}", chat_id, call.message.message_id)
            return

        if action == "key_change":
            if value == "yes":
                key_change_state[chat_id] = {"step": "waiting_new_key"}
                bot.edit_message_text("🔑 Отправь новый ключ:", chat_id, call.message.message_id)
            else:
                bot.edit_message_text("❌ Отмена.", chat_id, call.message.message_id)
                if chat_id in key_change_state: del key_change_state[chat_id]
    except Exception: pass

@bot.message_handler(content_types=['sticker'])
def handle_sticker(message):
    if is_duplicate(message): return
    file_id = message.sticker.file_id
    with model_lock:
        if file_id not in model.setdefault("stickers", []):
            model["stickers"].append(file_id)
            if len(model["stickers"]) > 500: model["stickers"] = model["stickers"][-500:]
            save_model_file()

@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    if is_duplicate(message): return
    answer = handle_voice_message(message)
    if isinstance(answer, str):
        thread_id = getattr(message, "message_thread_id", None)
        kwargs = {"reply_to_message_id": message.message_id}
        if thread_id: kwargs["message_thread_id"] = thread_id
        bot.send_message(message.chat.id, answer, **kwargs)

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
    force_sticker = bool(STICKER_RE.search(text))

    reply_to_bot = False
    replied_text = ""
    if message.reply_to_message:
        rm = message.reply_to_message
        if rm.from_user and bot_id is not None and rm.from_user.id == bot_id:
            reply_to_bot = True
            replied_text = preprocess_text(rm.text or rm.caption or "")

    with model_lock:
        if trigger and prompt: add_phrase(prompt, is_dialog=True)
        else: add_phrase(text, is_dialog=False)
        
        if reply_to_bot and text and replied_text:
            add_pair(replied_text, text)
        save_if_needed()
        model["meta"]["total_messages"] = int(model["meta"].get("total_messages", 0)) + 1

    should_reply = trigger or mentioned or reply_to_bot or (random.random() < CHAT_REPLY_CHANCE)
    if not should_reply: return

    query = prompt if (trigger and prompt) else text
    if mentioned and not trigger and bot_username:
        query = query.replace("@" + bot_username, "").replace(bot_username, "").strip()
    if not query and reply_to_bot: query = replied_text
    if not query: query = text

    s = get_settings(chat_id)
    user_name = from_user.first_name or "Пользователь"
    user_username = from_user.username or ""
    thread_id = getattr(message, "message_thread_id", None)
    
    try:
        bot.send_chat_action(chat_id, "typing")
        answer = await ask_ai(user_id, user_name, user_username, query, s.get("model", "openrouter"), s.get("mode", "normal"))
        
        if not answer:
            answer = "Братан я щас в туалете, мне лень отвечать"
        
        kwargs = {"reply_to_message_id": message.message_id}
        if thread_id: kwargs["message_thread_id"] = thread_id
        bot.send_message(chat_id, answer, **kwargs)
        
        if s.get("reactions", "on") == "on" and random.random() < REACTION_CHANCE:
            try:
                reaction = random.choice(ALLOWED_REACTIONS)
                url = f"https://api.telegram.org/bot{TOKEN}/setMessageReaction"
                requests.post(url, json={
                    "chat_id": chat_id, 
                    "message_id": message.message_id, 
                    "reaction": [{"type": "emoji", "emoji": reaction}]
                }, timeout=5)
            except Exception: pass

    except Exception as e:
        print(f"❌ Ошибка: {e}")
        kwargs = {"reply_to_message_id": message.message_id}
        if thread_id: kwargs["message_thread_id"] = thread_id
        bot.send_message(chat_id, "Братан я щас в туалете, мне лень отвечать", **kwargs)

@bot.message_handler(content_types=["text"])
def text_handler(message):
    global OPENROUTER_API_KEY
    if is_duplicate(message): return
    
    chat_id = message.chat.id
    if chat_id in key_change_state:
        state = key_change_state[chat_id]
        if state["step"] == "password":
            if message.text.strip() == KEY_PASSWORD:
                masked = OPENROUTER_API_KEY[:15] + "..." + OPENROUTER_API_KEY[-4:] if len(OPENROUTER_API_KEY) > 20 else "***"
                markup = types.InlineKeyboardMarkup(row_width=2)
                markup.add(
                    types.InlineKeyboardButton(" Сменить", callback_data="key_change:yes"),
                    types.InlineKeyboardButton("❌ Отмена", callback_data="key_change:no")
                )
                bot.send_message(chat_id, f" Текущий ключ:\n\n<code>{masked}</code>", reply_markup=markup, parse_mode="HTML")
                del key_change_state[chat_id]
            else:
                bot.send_message(chat_id, "❌ Неверный пароль.")
                del key_change_state[chat_id]
            return
        
        elif state["step"] == "waiting_new_key":
            new_key = message.text.strip()
            if new_key and (new_key.startswith("sk-or-v1-") or len(new_key) > 30):
                OPENROUTER_API_KEY = new_key
                with open(OPENROUTER_KEY_FILE, "w") as f: f.write(new_key)
                bot.send_message(chat_id, f"✅ Ключ обновлён!")
            else:
                bot.send_message(chat_id, "❌ Неверный формат.")
            del key_change_state[chat_id]
            return
        
        elif state["step"] == "waiting_custom_model":
            custom_model = message.text.strip()
            if len(custom_model) > 3:
                s = get_settings(chat_id)
                s["model"] = f"custom:{custom_model}"
                save_settings_chat(chat_id, s)
                bot.send_message(chat_id, f"✅ Модель: {custom_model}")
            del key_change_state[chat_id]
            return
    
    try:
        asyncio.run(process_message(message))
    except Exception as e:
        print(f"❌ Ошибка в text_handler: {e}")

def main():
    global bot_id, bot_username, OPENROUTER_API_KEY
    
    if os.path.exists(OPENROUTER_KEY_FILE):
        try:
            with open(OPENROUTER_KEY_FILE, "r") as f:
                key = f.read().strip()
            if key and len(key) > 10: OPENROUTER_API_KEY = key
        except Exception: pass
    
    load_model()
    load_settings()
    
    for _ in range(5):
        try:
            me = bot.get_me()
            bot_id = me.id
            bot_username = me.username
            break
        except Exception:
            time.sleep(3)
            
    print(f"✅ {BOT_TITLE} запущен. Username: @{bot_username}")
    print(f"🔑 Ключ: {OPENROUTER_API_KEY[:15]}...{OPENROUTER_API_KEY[-4:]}")
    print(f" Распознавание речи: {'OK' if SPEECH_AVAILABLE else 'НЕТ'}")
    
    atexit.register(save_all)
    try:
        bot.infinity_polling(skip_pending=True, allowed_updates=["message", "callback_query"])
    except KeyboardInterrupt: pass
    finally: save_all()

if __name__ == "__main__":
    main()
