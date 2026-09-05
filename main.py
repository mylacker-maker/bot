import logging
import sqlite3
import os
import random
import asyncio
import time
import threading
import urllib.request
import json
import base64
import io
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, MessageHandler,
                          CallbackQueryHandler, filters, ContextTypes)

TOKEN = "7894507440:AAGr5x8nxmdPh5ciP8g2WiuRccsbbC4EmgM"
DB = "peascards.db"
ADMIN_USERNAME = "lackeri"
FIREBASE_URL = "https://lackerteam-default-rtdb.firebaseio.com/peascards"

RARITIES = {
    'Обычный':      {'e': '⚪', 'price': 99},
    'Редкий':       {'e': '🟢', 'price': 179},
    'Эпический':    {'e': '🟣', 'price': 499},
    'Мифический':   {'e': '🔴', 'price': 999},
    'Легендарный':  {'e': '🟡', 'price': 4999},
    'Эксклюзивный': {'e': '🟠', 'price': 0}
}

UPGRADE_PATH = {
    'Обычный': 'Редкий',
    'Редкий': 'Эпический',
    'Эпический': 'Мифический',
    'Мифический': 'Легендарный',
    'Легендарный': 'Эксклюзивный'
}

UPGRADE_COSTS = {
    'Обычный': 250,
    'Редкий': 900,
    'Эпический': 1900,
    'Мифический': 4000,
    'Легендарный': 5000
}

FARM_INCOME = {
    'Обычный': 1,
    'Редкий': 2,
    'Эпический': 3,
    'Мифический': 5,
    'Легендарный': 8,
    'Эксклюзивный': 10
}

SPIN_EMOJIS = ['🍎','🍊','','🍇','🍉','','🍑','🍒','','🍌',
               '🌟','⭐','💎','🔥','️','🌈','🎯','','🃏','👑',
               '🏆','💰','','🔮','🧿','','🌸','🌺','','🐉',
               '','🐺','🦊','','🐶','🎃','','🤖','👾','']

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
user_states = {}
db_lock = threading.Lock()

# === FIREBASE ИНТЕГРАЦИЯ ===
def firebase_get(path):
    try:
        url = f"{FIREBASE_URL}/{path}.json"
        with urllib.request.urlopen(url) as response:
            data = response.read().decode()
            return json.loads(data) if data else None
    except Exception as e:
        logging.warning(f"Firebase GET error: {e}")
        return None

def firebase_put(path, data):
    try:
        url = f"{FIREBASE_URL}/{path}.json"
        req = urllib.request.Request(url, data=json.dumps(data).encode(), method='PUT')
        with urllib.request.urlopen(req) as response:
            return True
    except Exception as e:
        logging.warning(f"Firebase PUT error: {e}")
        return False

def firebase_patch(path, data):
    try:
        url = f"{FIREBASE_URL}/{path}.json"
        req = urllib.request.Request(url, data=json.dumps(data).encode(), method='PATCH')
        with urllib.request.urlopen(req) as response:
            return True
    except Exception as e:
        logging.warning(f"Firebase PATCH error: {e}")
        return False

def sync_users_to_firebase():
    """Синхронизирует всех пользователей с Firebase"""
    with db_lock:
        conn = db()
        try:
            users = conn.execute("SELECT * FROM users").fetchall()
            users_dict = {str(u['user_id']): dict(u) for u in users}
            firebase_put("users", users_dict)
        finally:
            conn.close()

def sync_cards_to_firebase():
    """Синхронизирует все карты с Firebase"""
    with db_lock:
        conn = db()
        try:
            cards = conn.execute("SELECT * FROM cards").fetchall()
            cards_dict = {str(c['id']): dict(c) for c in cards}
            firebase_put("cards", cards_dict)
        finally:
            conn.close()

def restore_from_firebase():
    """Восстанавливает данные из Firebase при запуске"""
    try:
        fb_users = firebase_get("users")
        fb_cards = firebase_get("cards")
        
        with db_lock:
            conn = db()
            try:
                # Восстанавливаем пользователей
                if fb_users:
                    for uid_str, u_data in fb_users.items():
                        uid = int(uid_str)
                        conn.execute("""
                            INSERT INTO users (user_id, username, name, coins, diamonds, chance, 
                            last_peas, last_epic_peas, is_admin, is_banned, farm_enabled, 
                            farm_card_id, farm_rarity)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(user_id) DO UPDATE SET
                            username=excluded.username, name=excluded.name, coins=excluded.coins, 
                            diamonds=excluded.diamonds, chance=excluded.chance, 
                            last_peas=excluded.last_peas, last_epic_peas=excluded.last_epic_peas,
                            is_admin=excluded.is_admin, is_banned=excluded.is_banned,
                            farm_enabled=excluded.farm_enabled, farm_card_id=excluded.farm_card_id,
                            farm_rarity=excluded.farm_rarity
                        """, (
                            uid, u_data.get('username'), u_data.get('name'), u_data.get('coins', 0),
                            u_data.get('diamonds', 0), u_data.get('chance', 10.0), 
                            u_data.get('last_peas', 0), u_data.get('last_epic_peas', 0),
                            u_data.get('is_admin', 0), u_data.get('is_banned', 0),
                            u_data.get('farm_enabled', 0), u_data.get('farm_card_id'), 
                            u_data.get('farm_rarity')
                        ))
                
                # Восстанавливаем карты
                if fb_cards:
                    for cid_str, c_data in fb_cards.items():
                        cid = int(cid_str)
                        conn.execute("""
                            INSERT INTO cards (id, name, photo_file_id, author, excl_limit, excl_count)
                            VALUES (?, ?, ?, ?, ?, ?)
                            ON CONFLICT(id) DO UPDATE SET
                            name=excluded.name, photo_file_id=excluded.photo_file_id,
                            author=excluded.author, excl_limit=excluded.excl_limit,
                            excl_count=excluded.excl_count
                        """, (
                            cid, c_data.get('name'), c_data.get('photo_file_id'),
                            c_data.get('author'), c_data.get('excl_limit', 0),
                            c_data.get('excl_count', 0)
                        ))
                
                conn.commit()
                logging.info("Данные восстановлены из Firebase")
            finally:
                conn.close()
    except Exception as e:
        logging.warning(f"Ошибка восстановления из Firebase: {e}")

# === БАЗА ДАННЫХ ===
def init_db():
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, name TEXT,
            coins INTEGER DEFAULT 0, diamonds INTEGER DEFAULT 0,
            chance REAL DEFAULT 10.0, last_peas REAL DEFAULT 0,
            last_epic_peas REAL DEFAULT 0,
            is_admin INTEGER DEFAULT 0, is_banned INTEGER DEFAULT 0,
            farm_enabled INTEGER DEFAULT 0, farm_card_id INTEGER, farm_rarity TEXT
        );
        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY, name TEXT,
            photo_file_id TEXT, author TEXT,
            excl_limit INTEGER DEFAULT 0, excl_count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS user_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            card_id INTEGER, rarity TEXT, quantity INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS market_listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, seller_id INTEGER,
            card_id INTEGER, rarity TEXT, quantity INTEGER,
            price_diamonds INTEGER, title TEXT
        );
        CREATE TABLE IF NOT EXISTS rest_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            card_name TEXT, photo_file_id TEXT, status TEXT DEFAULT 'pending'
        );
        CREATE TABLE IF NOT EXISTS support_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            user_message TEXT, admin_response TEXT,
            status TEXT DEFAULT 'open', created_at REAL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_user_cards_user ON user_cards(user_id);
        CREATE INDEX IF NOT EXISTS idx_market_card_rarity ON market_listings(card_id, rarity);
        CREATE INDEX IF NOT EXISTS idx_market_seller ON market_listings(seller_id);
    ''')
    conn.commit()
    conn.close()

def db():
    conn = sqlite3.connect(DB, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn

def get_user(uid):
    with db_lock:
        conn = db()
        try:
            r = conn.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
            return dict(r) if r else None
        finally:
            conn.close()

def get_user_by_username(username):
    with db_lock:
        conn = db()
        try:
            r = conn.execute("SELECT * FROM users WHERE LOWER(username)=LOWER(?)", (username.lstrip('@'),)).fetchone()
            return dict(r) if r else None
        finally:
            conn.close()

def ensure_user(uid, uname, name):
    with db_lock:
        conn = db()
        try:
            r = conn.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
            if not r:
                is_admin = 1 if uname and uname.lower() == ADMIN_USERNAME.lower() else 0
                conn.execute("INSERT INTO users (user_id,username,name,is_admin) VALUES (?,?,?,?)",
                             (uid, uname, name, is_admin))
                conn.commit()
                r = conn.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
                sync_users_to_firebase()
            elif uname and uname.lower() == ADMIN_USERNAME.lower() and not r['is_admin']:
                conn.execute("UPDATE users SET is_admin=1 WHERE user_id=?", (uid,))
                conn.commit()
                sync_users_to_firebase()
            return dict(r)
        finally:
            conn.close()

def update_user(uid, field, value):
    with db_lock:
        conn = db()
        try:
            conn.execute(f"UPDATE users SET {field}=? WHERE user_id=?", (value, uid))
            conn.commit()
            sync_users_to_firebase()
        finally:
            conn.close()

def add_coins(uid, amount):
    with db_lock:
        conn = db()
        try:
            conn.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (amount, uid))
            conn.commit()
            sync_users_to_firebase()
        finally:
            conn.close()

def add_diamonds(uid, amount):
    with db_lock:
        conn = db()
        try:
            conn.execute("UPDATE users SET diamonds=diamonds+? WHERE user_id=?", (amount, uid))
            conn.commit()
            sync_users_to_firebase()
        finally:
            conn.close()

def get_all_cards():
    with db_lock:
        conn = db()
        try:
            rows = conn.execute("SELECT * FROM cards").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

def get_user_cards(uid):
    with db_lock:
        conn = db()
        try:
            rows = conn.execute(
                "SELECT uc.*, c.name as card_name, c.photo_file_id, c.excl_limit "
                "FROM user_cards uc JOIN cards c ON uc.card_id=c.id WHERE uc.user_id=? AND uc.quantity>0",
                (uid,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

def add_user_card(uid, card_id, rarity, qty=1):
    with db_lock:
        conn = db()
        try:
            existing = conn.execute(
                "SELECT id FROM user_cards WHERE user_id=? AND card_id=? AND rarity=?",
                (uid, card_id, rarity)
            ).fetchone()
            if existing:
                conn.execute("UPDATE user_cards SET quantity=quantity+? WHERE id=?", (qty, existing['id']))
            else:
                conn.execute(
                    "INSERT INTO user_cards (user_id,card_id,rarity,quantity) VALUES (?,?,?,?)",
                    (uid, card_id, rarity, qty)
                )
            conn.commit()
        finally:
            conn.close()

def remove_user_card(uid, card_id, rarity, qty=1):
    with db_lock:
        conn = db()
        try:
            existing = conn.execute(
                "SELECT id,quantity FROM user_cards WHERE user_id=? AND card_id=? AND rarity=?",
                (uid, card_id, rarity)
            ).fetchone()
            if not existing or existing["quantity"] < qty:
                return False
            conn.execute("UPDATE user_cards SET quantity=quantity-? WHERE id=?", (qty, existing["id"]))
            conn.commit()
            return True
        finally:
            conn.close()

def claim_card(uid, card_id, rarity):
    with db_lock:
        conn = db()
        try:
            card = conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
            if not card:
                return None

            if rarity == "Эксклюзивный":
                if card['excl_limit'] <= 0:
                    return None
                updated = conn.execute(
                    "UPDATE cards SET excl_count=excl_count+1 WHERE id=? AND excl_count < excl_limit",
                    (card_id,)
                )
                if updated.rowcount != 1:
                    conn.rollback()
                    return None
                sync_cards_to_firebase()

            existing = conn.execute(
                "SELECT id FROM user_cards WHERE user_id=? AND card_id=? AND rarity=?",
                (uid, card_id, rarity)
            ).fetchone()
            if existing:
                conn.execute("UPDATE user_cards SET quantity=quantity+1 WHERE id=?", (existing["id"],))
            else:
                conn.execute(
                    "INSERT INTO user_cards (user_id,card_id,rarity,quantity) VALUES (?,?,?,1)",
                    (uid, card_id, rarity)
                )
            conn.commit()
            return dict(card)
        except Exception as e:
            logging.error(f"Error in claim_card: {e}")
            conn.rollback()
            return None
        finally:
            conn.close()

def upgrade_card(uid, card_id, from_rarity):
    if from_rarity not in UPGRADE_PATH:
        return False, None, "Максимальная редкость"
    
    to_rarity = UPGRADE_PATH[from_rarity]
    cost = UPGRADE_COSTS[from_rarity]
    
    with db_lock:
        conn = db()
        try:
            user = conn.execute("SELECT coins FROM users WHERE user_id=?", (uid,)).fetchone()
            if not user or user['coins'] < cost:
                return False, None, "Недостаточно монет"
            
            if to_rarity == "Эксклюзивный":
                card = conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
                if not card:
                    return False, None, "Карта не найдена"
                if card['excl_limit'] <= 0:
                    return False, None, "У этой карты нет эксклюзивных копий"
                if card['excl_count'] >= card['excl_limit']:
                    return False, None, "Эксклюзивные копии закончились"
                
                if random.random() > 0.3:
                    conn.execute("UPDATE users SET coins=coins-? WHERE user_id=?", (cost, uid))
                    conn.commit()
                    sync_users_to_firebase()
                    return False, None, f"Попытка не удалась (шанс 30%). Списано {cost}$"
                
                conn.execute("UPDATE cards SET excl_count=excl_count+1 WHERE id=?", (card_id,))
                sync_cards_to_firebase()
            
            conn.execute(
                "UPDATE user_cards SET quantity=quantity-1 WHERE user_id=? AND card_id=? AND rarity=? AND quantity>0",
                (uid, card_id, from_rarity)
            )
            
            existing = conn.execute(
                "SELECT id FROM user_cards WHERE user_id=? AND card_id=? AND rarity=?",
                (uid, card_id, to_rarity)
            ).fetchone()
            if existing:
                conn.execute("UPDATE user_cards SET quantity=quantity+1 WHERE id=?", (existing['id'],))
            else:
                conn.execute(
                    "INSERT INTO user_cards (user_id,card_id,rarity,quantity) VALUES (?,?,?,1)",
                    (uid, card_id, to_rarity)
                )
            
            conn.execute("UPDATE users SET coins=coins-? WHERE user_id=?", (cost, uid))
            conn.commit()
            sync_users_to_firebase()
            return True, to_rarity, None
        except Exception as e:
            logging.error(f"Error in upgrade_card: {e}")
            conn.rollback()
            return False, None, str(e)
        finally:
            conn.close()

# === ЛОГИКА ИГРЫ ===
def roll_rarity(chance, card):
    roll = random.uniform(0, 100)
    if roll > chance:
        return 'Обычный'
    sub = random.uniform(0, 100)
    if sub < 50: return 'Редкий'
    if sub < 75: return 'Эпический'
    if sub < 90: return 'Мифический'
    if sub < 98: return 'Легендарный'
    if card['excl_limit'] > 0 and card['excl_count'] < card['excl_limit']:
        return 'Эксклюзивный'
    return 'Легендарный'

def pick_card():
    cards = get_all_cards()
    if not cards: return None
    return random.choice(cards)

def pick_three_cards():
    cards = get_all_cards()
    if not cards: return []
    if len(cards) < 3:
        return [random.choice(cards) for _ in range(3)]
    return random.sample(cards, 3)

def gen_spin():
    return ''.join(random.sample(SPIN_EMOJIS, 3))

def is_admin(uid):
    u = get_user(uid)
    return u and u['is_admin'] == 1

# === ФОНОВЫЕ ЗАДАЧИ ===
async def farm_loop(app):
    while True:
        try:
            await asyncio.sleep(300)
            with db_lock:
                conn = db()
                try:
                    users = conn.execute(
                        "SELECT user_id, farm_rarity FROM users WHERE farm_enabled=1 AND farm_card_id IS NOT NULL"
                    ).fetchall()
                    for u in users:
                        income = FARM_INCOME.get(u['farm_rarity'], 0)
                        if income > 0:
                            conn.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (income, u['user_id']))
                    conn.commit()
                    sync_users_to_firebase()
                finally:
                    conn.close()
        except Exception as e:
            logging.error(f"Farm loop error: {e}")
            await asyncio.sleep(60)

# === ХЕЛПЕРЫ ===
async def safe_del(msg):
    try: await msg.delete()
    except: pass

def parse_username(text):
    for word in text.split():
        if word.startswith('@'):
            return word.lstrip('@')
    return None

# === КОМАНДЫ ===
async def start_cmd(update: Update, ctx):
    u = ensure_user(update.effective_user.id, update.effective_user.username, update.effective_user.first_name)
    text = (
        "Добро пожаловать в PeasCards.\n\n"
        "Это коллекционная игра с редкостями, шансами и рынком.\n\n"
        "Основные действия:\n"
        "• Горох — открыть попытку получения карты\n"
        "• Инвентарь — ваша коллекция карт\n"
        "• Профиль — статистика аккаунта\n"
        "• Ответьте словом Профиль на сообщение игрока для просмотра его профиля\n\n"
        "Команды:\n"
        "/shop — магазин\n"
        "/market — рынок\n"
        "/sell — продать карту на рынке\n"
        "/rest — предложить новую карту для игры\n"
        "/farm — автоматическая ферма монет\n"
        "/leaders — топ игроков\n"
        "/teh — техподдержка"
    )
    await update.message.reply_text(text)

async def adm_cmd(update: Update, ctx):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username, update.effective_user.first_name)
    if not is_admin(uid):
        await update.message.reply_text("Команда не найдена.")
        return
    text = (
        "Панель администратора:\n\n"
        "/admin — управление картами и пулом\n"
        "/addcard — добавить карту напрямую (без модерации)\n"
        "/allcards — все карты с информацией об эксклюзивах\n"
        "/extlimit — продлить лимит эксклюзивных копий карты\n"
        "/resel @username количество — начислить монеты пользователю\n"
        "/resel @all количество — начислить монеты ВСЕМ игрокам\n"
        "/money @username количество — начислить монеты (или reply на сообщение)\n"
        "/give — выдать карты пользователю\n"
        "/ungive — изъять карты у пользователя\n"
        "/sp @username — посмотреть все карты пользователя"
    )
    await update.message.reply_text(text)

async def admin_cmd(update: Update, ctx):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username, update.effective_user.first_name)
    if not is_admin(uid):
        await update.message.reply_text("Команда не найдена.")
        return
    cards = get_all_cards()
    text = f"Админ-панель. Всего карт в пуле: {len(cards)}\n\n"
    for c in cards:
        excl = f"Эксклюзив: {c['excl_count']}/{c['excl_limit']}" if c['excl_limit'] > 0 else "Без эксклюзива"
        text += f"• {c['name']} ({excl})\n"
    text += "\nИспользуйте /addcard для добавления новой карты."
    await update.message.reply_text(text)

async def addcard_cmd(update: Update, ctx):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username, update.effective_user.first_name)
    if not is_admin(uid):
        await update.message.reply_text("Команда не найдена.")
        return
    user_states[uid] = {'action': 'addcard_name'}
    await update.message.reply_text("Введите название новой карты:")

async def allcards_cmd(update: Update, ctx):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username, update.effective_user.first_name)
    if not is_admin(uid):
        await update.message.reply_text("Команда не найдена.")
        return
    cards = get_all_cards()
    if not cards:
        await update.message.reply_text("Карт пока нет.")
        return
    text = "Все карты:\n\n"
    for c in cards:
        excl_info = "Без эксклюзива"
        if c['excl_limit'] > 0:
            remaining = c['excl_limit'] - c['excl_count']
            excl_info = f"Эксклюзив: {c['excl_count']}/{c['excl_limit']} (осталось: {remaining})"
        text += f"{c['name']} (ID: {c['id']})\n{excl_info}\n\n"
    await update.message.reply_text(text)

async def extlimit_cmd(update: Update, ctx):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username, update.effective_user.first_name)
    if not is_admin(uid):
        await update.message.reply_text("Команда не найдена.")
        return
    cards = get_all_cards()
    kb = [[InlineKeyboardButton(c['name'], callback_data=f"extl_pick:{uid}:{c['id']}")] for c in cards if c['excl_limit'] > 0]
    if not kb:
        await update.message.reply_text("Нет карт с эксклюзивными копиями.")
        return
    kb.append([InlineKeyboardButton("Отмена", callback_data=f"noop")])
    await update.message.reply_text("Выберите карту для продления лимита:", reply_markup=InlineKeyboardMarkup(kb))

async def resel_cmd(update: Update, ctx):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username, update.effective_user.first_name)
    if not is_admin(uid):
        await update.message.reply_text("Команда не найдена.")
        return
    args = update.message.text.split()
    if len(args) < 3:
        await update.message.reply_text("Использование: /resel @username количество или /resel @all количество")
        return
    
    target_identifier = args[1].lower()
    try:
        amount = int(args[2])
    except ValueError:
        await update.message.reply_text("Количество должно быть числом.")
        return

    if target_identifier == '@all':
        with db_lock:
            conn = db()
            try:
                conn.execute("UPDATE users SET coins = coins + ?", (amount,))
                conn.commit()
                sync_users_to_firebase()
            finally:
                conn.close()
        await update.message.reply_text(f"Начислено {amount}$ всем игрокам.")
        return

    username = target_identifier.lstrip('@')
    target = get_user_by_username(username)
    if not target:
        await update.message.reply_text("Пользователь не найден.")
        return
    add_coins(target['user_id'], amount)
    await update.message.reply_text(f"Начислено {amount}$ пользователю @{username}")
    try:
        await ctx.bot.send_message(target['user_id'], f"Вам начислено {amount}$")
    except: pass

async def give_cmd(update: Update, ctx):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username, update.effective_user.first_name)
    if not is_admin(uid):
        await update.message.reply_text("Команда не найдена.")
        return
    cards = get_all_cards()
    if not cards:
        await update.message.reply_text("Карт пока нет.")
        return
    user_states[uid] = {'action': 'give_card'}
    kb = [[InlineKeyboardButton(c['name'], callback_data=f"give_pick:{uid}:{c['id']}")] for c in cards]
    kb.append([InlineKeyboardButton("Отмена", callback_data=f"noop")])
    await update.message.reply_text("Выберите карту:", reply_markup=InlineKeyboardMarkup(kb))

async def sp_cmd(update: Update, ctx):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username, update.effective_user.first_name)
    if not is_admin(uid):
        await update.message.reply_text("Команда не найдена.")
        return
    args = update.message.text.split()
    if len(args) < 2:
        await update.message.reply_text("Использование: /sp @username")
        return
    username = args[1].lstrip('@')
    target = get_user_by_username(username)
    if not target:
        await update.message.reply_text("Пользователь не найден.")
        return
    cards = get_user_cards(target['user_id'])
    if not cards:
        await update.message.reply_text(f"У @{username} нет карт.")
        return
    text = f"Карты @{username}:\n\n"
    for c in cards:
        ri = RARITIES[c['rarity']]
        text += f"{ri['e']} {c['card_name']} — {c['rarity']} x{c['quantity']}\n"
    await update.message.reply_text(text)

async def ungive_cmd(update: Update, ctx):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username, update.effective_user.first_name)
    if not is_admin(uid):
        await update.message.reply_text("Команда не найдена.")
        return
    cards = get_all_cards()
    if not cards:
        await update.message.reply_text("Карт пока нет.")
        return
    user_states[uid] = {'action': 'ungive_card'}
    kb = [[InlineKeyboardButton(c['name'], callback_data=f"ungive_pick:{uid}:{c['id']}")] for c in cards]
    kb.append([InlineKeyboardButton("Отмена", callback_data=f"noop")])
    await update.message.reply_text("Выберите карту:", reply_markup=InlineKeyboardMarkup(kb))

async def money_cmd(update: Update, ctx):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username, update.effective_user.first_name)
    if not is_admin(uid):
        await update.message.reply_text("Команда не найдена.")
        return
    
    args = update.message.text.split()
    target_id = None
    
    if update.message.reply_to_message:
        target_id = update.message.reply_to_message.from_user.id
        if len(args) < 2:
            await update.message.reply_text("Использование: /money количество (ответом на сообщение)")
            return
        try:
            amount = int(args[1])
        except ValueError:
            await update.message.reply_text("Количество должно быть числом.")
            return
    else:
        if len(args) < 3:
            await update.message.reply_text("Использование: /money @username количество")
            return
        username = args[1].lstrip('@')
        target = get_user_by_username(username)
        if not target:
            await update.message.reply_text("Пользователь не найден.")
            return
        target_id = target['user_id']
        try:
            amount = int(args[2])
        except ValueError:
            await update.message.reply_text("Количество должно быть числом.")
            return
    
    add_coins(target_id, amount)
    target = get_user(target_id)
    await update.message.reply_text(f"Начислено {amount}$ пользователю {target['name']}")
    try:
        await ctx.bot.send_message(target_id, f"Вам начислено {amount}$")
    except: pass

async def shop_cmd(update: Update, ctx):
    uid = update.effective_user.id
    u = ensure_user(uid, update.effective_user.username, update.effective_user.first_name)
    if u['is_banned']:
        await update.message.reply_text("Доступ ограничен.")
        return
    
    # Рассчитываем время до следующего эпического горошка
    now = time.time()
    epic_cooldown = 86400 - (now - u['last_epic_peas'])
    if epic_cooldown < 0:
        epic_cooldown = 0
    hours = int(epic_cooldown // 3600)
    minutes = int((epic_cooldown % 3600) // 60)
    wait_text = f"{hours}ч {minutes}мин" if epic_cooldown > 0 else "Доступен"
    
    kb = [
        [InlineKeyboardButton("Улучшение шанса", callback_data=f"shop_chance:{uid}")],
        [InlineKeyboardButton("Обмен валюты", callback_data=f"shop_exchange:{uid}")],
        [InlineKeyboardButton(f"Эпический горошек (1000$) - {wait_text}", callback_data=f"shop_epic:{uid}")],
    ]
    await update.message.reply_text(
        f"Магазин.\nБаланс: {u['coins']}$. Алмазы: {u['diamonds']}.\nШанс: {u['chance']}%",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def rest_cmd(update: Update, ctx):
    uid = update.effective_user.id
    u = ensure_user(uid, update.effective_user.username, update.effective_user.first_name)
    if u['is_banned']:
        await update.message.reply_text("Доступ ограничен.")
        return
    user_states[uid] = {'action': 'rest_name'}
    await update.message.reply_text("Введите название карты, которую хотите предложить:")

async def market_cmd(update: Update, ctx):
    uid = update.effective_user.id
    u = ensure_user(uid, update.effective_user.username, update.effective_user.first_name)
    if u['is_banned']:
        await update.message.reply_text("Доступ ограничен.")
        return
    with db_lock:
        conn = db()
        try:
            total = conn.execute("SELECT COUNT(*) as c FROM market_listings").fetchone()['c']
            my = conn.execute("SELECT COUNT(*) as c FROM market_listings WHERE seller_id=?", (uid,)).fetchone()['c']
        finally:
            conn.close()
    kb = [
        [InlineKeyboardButton("Рынок", callback_data=f"mkt_new:{uid}:0")],
        [InlineKeyboardButton("Рынок по категориям", callback_data=f"mkt_cards:{uid}")],
        [InlineKeyboardButton("Обмен валюты", callback_data=f"mkt_exchange:{uid}")],
    ]
    await update.message.reply_text(
        f"Рынок.\nАктивных объявлений: {total}\nВаших объявлений: {my}\n\n/sell — продать карту",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def sell_cmd(update: Update, ctx):
    uid = update.effective_user.id
    u = ensure_user(uid, update.effective_user.username, update.effective_user.first_name)
    if u['is_banned']:
        await update.message.reply_text("Доступ ограничен.")
        return
    cards = get_user_cards(uid)
    if not cards:
        await update.message.reply_text("У вас нет карт для продажи.")
        return
    seen = set()
    kb = []
    for c in cards:
        if c['card_id'] not in seen:
            seen.add(c['card_id'])
            kb.append([InlineKeyboardButton(c['card_name'], callback_data=f"sell_pick:{uid}:{c['card_id']}")])
    kb.append([InlineKeyboardButton("Отмена", callback_data=f"sell_cancel:{uid}")])
    await update.message.reply_text("Выберите карту для продажи:", reply_markup=InlineKeyboardMarkup(kb))

async def farm_cmd(update: Update, ctx):
    uid = update.effective_user.id
    u = ensure_user(uid, update.effective_user.username, update.effective_user.first_name)
    if u['is_banned']:
        await update.message.reply_text("Доступ ограничен.")
        return
    
    cards = get_user_cards(uid)
    if not cards:
        await update.message.reply_text("Для фермы нужна хотя бы одна карта. Получите первую карту через команду Горох.")
        return
    
    farm_status = "Активна" if u['farm_enabled'] else "Неактивна"
    farm_card_info = "Не установлена"
    
    if u['farm_card_id'] and u['farm_rarity']:
        with db_lock:
            conn = db()
            try:
                card = conn.execute("SELECT * FROM cards WHERE id=?", (u['farm_card_id'],)).fetchone()
                if card:
                    ri = RARITIES[u['farm_rarity']]
                    income = FARM_INCOME.get(u['farm_rarity'], 0)
                    farm_card_info = f"{card['name']} {ri['e']} ({income}$ за 5 мин)"
            finally:
                conn.close()
    
    toggle_btn = "Отключить" if u['farm_enabled'] else "Включить"
    kb = [
        [InlineKeyboardButton("Сменить карточку", callback_data=f"farm_change:{uid}")],
        [InlineKeyboardButton(f"{toggle_btn} ферму", callback_data=f"farm_toggle:{uid}")],
    ]
    
    text = (
        f"Автоматическая ферма.\n"
        f"Статус: {farm_status}\n"
        f"Карта: {farm_card_info}\n\n"
        f"Ферма начисляет монеты каждые 5 минут в зависимости от редкости карты."
    )
    
    # ИСПРАВЛЕНИЕ: Отправляем новое сообщение вместо редактирования
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))

# === ОСТАЛЬНЫЕ КОМАНДЫ И ОБРАБОТЧИКИ ===
# (Продолжение кода с остальными функциями - leaders_cmd, teh_cmd, handle_text, handle_photo, button_handler и т.д.)
# Из-за ограничения длины я покажу только ключевые исправления

# В функции button_handler для farm_toggle:
async def button_handler(update: Update, ctx):
    query = update.callback_query
    await query.answer()
    data = query.data
    parts = data.split(":")
    action = parts[0]
    uid = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0

    if action == "noop": return
    
    if uid and action not in {"admin_acc", "admin_rej", "admin_ban", "sup_reply"}:
        if query.from_user.id != uid:
            await query.answer("Эта кнопка для другого игрока.", show_alert=True)
            return

    # === ФЕРМА - ИСПРАВЛЕНИЕ ===
    if action == "farm_toggle":
        u = get_user(uid)
        if not u['farm_card_id']:
            await query.answer("Сначала установите карточку в ферму.", show_alert=True)
            return
        
        new_status = 0 if u['farm_enabled'] else 1
        update_user(uid, 'farm_enabled', new_status)
        status_text = "Активна" if new_status else "Неактивна"
        await query.answer(f"Ферма: {status_text}")
        
        # ИСПРАВЛЕНИЕ: Удаляем старое сообщение и отправляем новое
        await safe_del(query.message)
        await farm_cmd(update, ctx)  # Это отправит новое сообщение с правильным статусом
        return

    # === МАГАЗИН - ЭПИЧЕСКИЙ ГОРОШЕК ===
    if action == "shop_epic":
        u = get_user(uid)
        now = time.time()
        cooldown = 86400 - (now - u['last_epic_peas'])
        
        if cooldown > 0:
            hours = int(cooldown // 3600)
            minutes = int((cooldown % 3600) // 60)
            await query.answer(f"Доступен через {hours}ч {minutes}мин", show_alert=True)
            return
            
        if u['coins'] < 1000:
            await query.answer("Недостаточно монет. Нужно 1000$.", show_alert=True)
            return
        
        # ... остальной код эпического горошка

    # === REST - ОТПРАВИТЬ ===
    if action == "rest_send":
        state = user_states.get(uid)
        if not state: 
            await query.answer("Ошибка: состояние не найдено", show_alert=True)
            return
        
        with db_lock:
            conn = db()
            try:
                conn.execute("INSERT INTO rest_requests (user_id,card_name,photo_file_id) VALUES (?,?,?)", 
                           (uid, state['card_name'], state['photo_file_id']))
                req_id = conn.execute("SELECT last_insert_rowid()")[0]
                conn.commit()
            finally:
                conn.close()
        
        user_states.pop(uid, None)
        await safe_del(query.message)
        await query.message.chat.send_message("Заявка отправлена на модерацию.")
        
        # Отправка админу
        admin = get_user_by_username(ADMIN_USERNAME)
        if admin:
            u = get_user(uid)
            uname = f"@{u['username']}" if u['username'] else u['name']
            caption = f"Заявка от {u['name']} ({uname})\nКарта: {state['card_name']}"
            kb = [
                [InlineKeyboardButton("Принять", callback_data=f"admin_acc:{admin['user_id']}:{req_id}")],
                [InlineKeyboardButton("Отклонить", callback_data=f"admin_rej:{admin['user_id']}:{req_id}")],
                [InlineKeyboardButton("Заблокировать", callback_data=f"admin_ban:{admin['user_id']}:{req_id}")],
            ]
            try:
                if state.get('photo_file_id'):
                    await ctx.bot.send_photo(admin['user_id'], photo=state['photo_file_id'], 
                                           caption=caption, reply_markup=InlineKeyboardMarkup(kb))
                else:
                    await ctx.bot.send_message(admin['user_id'], caption, reply_markup=InlineKeyboardMarkup(kb))
            except Exception as e:
                logging.error(f"Error sending to admin: {e}")
        return

    # ... остальной код button_handler для других кнопок

# === ТОЧКА ВХОДА ===
async def post_init(app):
    asyncio.create_task(farm_loop(app))

def main():
    init_db()
    restore_from_firebase()  # Восстанавливаем данные из Firebase
    
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("adm", adm_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("addcard", addcard_cmd))
    app.add_handler(CommandHandler("allcards", allcards_cmd))
    app.add_handler(CommandHandler("extlimit", extlimit_cmd))
    app.add_handler(CommandHandler("resel", resel_cmd))
    app.add_handler(CommandHandler("give", give_cmd))
    app.add_handler(CommandHandler("sp", sp_cmd))
    app.add_handler(CommandHandler("ungive", ungive_cmd))
    app.add_handler(CommandHandler("money", money_cmd))
    app.add_handler(CommandHandler("shop", shop_cmd))
    app.add_handler(CommandHandler("rest", rest_cmd))
    app.add_handler(CommandHandler("market", market_cmd))
    app.add_handler(CommandHandler("sell", sell_cmd))
    app.add_handler(CommandHandler("farm", farm_cmd))
    app.add_handler(CommandHandler("leaders", leaders_cmd))
    app.add_handler(CommandHandler("teh", teh_cmd))
    
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    app.post_init = post_init
    
    logging.info("PeasCards запущен с Firebase")
    app.run_polling()

if __name__ == '__main__':
    main()