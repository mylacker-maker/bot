import logging
import sqlite3
import os
import random
import asyncio
import time
import threading
import urllib.request
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (Application, CommandHandler, MessageHandler,
                          CallbackQueryHandler, filters, ContextTypes)

def load_token():
    token = os.environ.get("BOT_TOKEN")
    if token:
        return token
    try:
        from google.colab import userdata
        return userdata.get("BOT_TOKEN")
    except (ImportError, AttributeError):
        return None

TOKEN = load_token()
if not TOKEN:
    raise RuntimeError("Добавьте секрет BOT_TOKEN")

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

SPIN_EMOJIS = ['🍎','🍊','🍋','🍇','🍉','🍓','🍑','🍒','🥝','🍌',
               '🌟','⭐','💎','🔥','❄️','🌈','🎯','🎲','🃏','👑',
               '🏆','💰','🎁','🔮','🧿','🍀','🌸','🌺','🦋','🐉',
               '🦄','🐺','🦊','🐱','🐶','🎃','👻','🤖','👾','🎮']

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
user_states = {}
db_lock = threading.Lock()

# === FIREBASE ИНТЕГРАЦИЯ ===
def restore_from_firebase():
    """Восстанавливает данные пользователей из Firebase при запуске"""
    try:
        url = f"{FIREBASE_URL}/users.json"
        with urllib.request.urlopen(url) as response:
            data = response.read().decode()
            fb_users = json.loads(data) if data else {}
        
        if not fb_users:
            logging.info("Firebase пуст, используем локальную базу")
            return False
            
        with db_lock:
            conn = db()
            try:
                for uid_str, u_data in fb_users.items():
                    uid = int(uid_str)
                    conn.execute("""
                        INSERT INTO users (user_id, username, name, coins, diamonds, chance, last_peas, last_epic_peas, is_admin, is_banned, farm_enabled, farm_card_id, farm_rarity)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(user_id) DO UPDATE SET
                        username=excluded.username, name=excluded.name, coins=excluded.coins, 
                        diamonds=excluded.diamonds, chance=excluded.chance, last_peas=excluded.last_peas,
                        last_epic_peas=excluded.last_epic_peas, is_admin=excluded.is_admin, 
                        is_banned=excluded.is_banned, farm_enabled=excluded.farm_enabled,
                        farm_card_id=excluded.farm_card_id, farm_rarity=excluded.farm_rarity
                    """, (
                        uid, u_data.get('username'), u_data.get('name'), u_data.get('coins', 0),
                        u_data.get('diamonds', 0), u_data.get('chance', 10.0), u_data.get('last_peas', 0),
                        u_data.get('last_epic_peas', 0), u_data.get('is_admin', 0), u_data.get('is_banned', 0),
                        u_data.get('farm_enabled', 0), u_data.get('farm_card_id'), u_data.get('farm_rarity')
                    ))
                conn.commit()
                logging.info("Данные успешно восстановлены из Firebase")
                return True
            finally:
                conn.close()
    except Exception as e:
        logging.warning(f"Ошибка восстановления из Firebase (проверьте правила доступа): {e}")
        return False

def sync_to_firebase():
    """Синхронизирует всю таблицу пользователей с Firebase"""
    try:
        with db_lock:
            conn = db()
            try:
                users = conn.execute("SELECT * FROM users").fetchall()
                users_dict = {str(u['user_id']): dict(u) for u in users}
            finally:
                conn.close()
        
        url = f"{FIREBASE_URL}/users.json"
        req = urllib.request.Request(url, data=json.dumps(users_dict).encode(), method='PUT')
        with urllib.request.urlopen(req) as response:
            logging.info("Данные синхронизированы с Firebase")
            return True
    except Exception as e:
        logging.warning(f"Ошибка синхронизации с Firebase: {e}")
        return False

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
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT,
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
            elif uname and uname.lower() == ADMIN_USERNAME.lower() and not r['is_admin']:
                conn.execute("UPDATE users SET is_admin=1 WHERE user_id=?", (uid,))
                conn.commit()
            return dict(r)
        finally:
            conn.close()

def update_user(uid, field, value):
    with db_lock:
        conn = db()
        try:
            conn.execute(f"UPDATE users SET {field}=? WHERE user_id=?", (value, uid))
            conn.commit()
        finally:
            conn.close()

def add_coins(uid, amount):
    with db_lock:
        conn = db()
        try:
            conn.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (amount, uid))
            conn.commit()
        finally:
            conn.close()

def add_diamonds(uid, amount):
    with db_lock:
        conn = db()
        try:
            conn.execute("UPDATE users SET diamonds=diamonds+? WHERE user_id=?", (amount, uid))
            conn.commit()
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
                    return False, None, f"Попытка не удалась (шанс 30%). Списано {cost}$"
                
                conn.execute("UPDATE cards SET excl_count=excl_count+1 WHERE id=?", (card_id,))
            
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
                finally:
                    conn.close()
            sync_to_firebase() # Синхронизируем после начисления
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
            finally:
                conn.close()
        sync_to_firebase()
        await update.message.reply_text(f"Начислено {amount}$ всем игрокам.")
        return

    username = target_identifier.lstrip('@')
    target = get_user_by_username(username)
    if not target:
        await update.message.reply_text("Пользователь не найден.")
        return
    add_coins(target['user_id'], amount)
    sync_to_firebase()
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
    sync_to_firebase()
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
    kb = [
        [InlineKeyboardButton("Улучшение шанса", callback_data=f"shop_chance:{uid}")],
        [InlineKeyboardButton("Обмен валюты", callback_data=f"shop_exchange:{uid}")],
        [InlineKeyboardButton("Эпический горошек (1000$)", callback_data=f"shop_epic:{uid}")],
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
    farm_photo = None
    if u['farm_card_id'] and u['farm_rarity']:
        with db_lock:
            conn = db()
            try:
                card = conn.execute("SELECT * FROM cards WHERE id=?", (u['farm_card_id'],)).fetchone()
                if card:
                    ri = RARITIES[u['farm_rarity']]
                    income = FARM_INCOME.get(u['farm_rarity'], 0)
                    farm_card_info = f"{card['name']} {ri['e']} ({income}$ за 5 мин)"
                    farm_photo = card['photo_file_id']
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
    
    if farm_photo:
        await update.message.reply_photo(photo=farm_photo, caption=text, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def leaders_cmd(update: Update, ctx):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_user.username, update.effective_user.first_name)
    with db_lock:
        conn = db()
        try:
            leaders = conn.execute(
                "SELECT user_id, name, username, coins FROM users WHERE is_banned=0 ORDER BY coins DESC LIMIT 10"
            ).fetchall()
        finally:
            conn.close()
    
    if not leaders:
        await update.message.reply_text("Пока нет игроков.")
        return
    
    text = "Топ-10 игроков по монетам:\n\n"
    for i, l in enumerate(leaders, 1):
        medal = "1. " if i == 1 else "2. " if i == 2 else "3. " if i == 3 else f"{i}. "
        uname = f"@{l['username']}" if l['username'] else l['name']
        text += f"{medal}{uname} — {l['coins']}$\n"
    
    await update.message.reply_text(text)

async def teh_cmd(update: Update, ctx):
    uid = update.effective_user.id
    u = ensure_user(uid, update.effective_user.username, update.effective_user.first_name)
    if u['is_banned']:
        await update.message.reply_text("Доступ ограничен.")
        return
    user_states[uid] = {'action': 'support_msg'}
    await update.message.reply_text("Напишите ваше сообщение для техподдержки:")

# === ОБРАБОТКА ТЕКСТА ===
async def handle_text(update: Update, ctx):
    if not update.message or not update.message.text:
        return
    uid = update.effective_user.id
    text = update.message.text.strip()
    u = ensure_user(uid, update.effective_user.username, update.effective_user.first_name)
    
    if text.lower() == 'магазин':
        await shop_cmd(update, ctx)
        return
    if text.lower() == 'рынок':
        await market_cmd(update, ctx)
        return
    if text.lower() == 'ферма':
        await farm_cmd(update, ctx)
        return
    if text.lower() in ['техподдержка', 'тех поддержка']:
        await teh_cmd(update, ctx)
        return
    if text.lower() == 'лидеры':
        await leaders_cmd(update, ctx)
        return

    if text.lower() == 'профиль' and update.message.reply_to_message:
        target = update.message.reply_to_message.from_user
        if not target: return
        tu = get_user(target.id)
        if not tu:
            await update.message.reply_text("Пользователь не зарегистрирован.")
            return
        tc = get_user_cards(target.id)
        total = sum(c['quantity'] for c in tc)
        await update.message.reply_text(
            f"Профиль {tu['name']}\nМонеты: {tu['coins']}$. Алмазы: {tu['diamonds']}\nШанс: {tu['chance']}%\nКарт: {total}")
        return

    if text.lower() == 'профиль':
        tc = get_user_cards(uid)
        total = sum(c['quantity'] for c in tc)
        await update.message.reply_text(
            f"Ваш профиль\nМонеты: {u['coins']}$. Алмазы: {u['diamonds']}\nШанс: {u['chance']}%\nКарт: {total}")
        return

    if text.lower() == 'инвентарь':
        cards = get_user_cards(uid)
        if not cards:
            await update.message.reply_text("Инвентарь пуст.")
            return
        rarity_order = {'Эксклюзивный': 0, 'Легендарный': 1, 'Мифический': 2, 'Эпический': 3, 'Редкий': 4, 'Обычный': 5}
        cards_sorted = sorted(cards, key=lambda c: rarity_order.get(c['rarity'], 99))
        
        kb = []
        for c in cards_sorted:
            ri = RARITIES[c['rarity']]
            btn_text = f"{c['card_name']} {ri['e']}, {c['quantity']}"
            kb.append([InlineKeyboardButton(btn_text, callback_data=f"inv_item:{uid}:{c['card_id']}:{c['rarity']}")])
        kb.append([InlineKeyboardButton("Закрыть", callback_data=f"noop")])
        await update.message.reply_text("Инвентарь", reply_markup=InlineKeyboardMarkup(kb))
        return

    if 'горох' in text.lower():
        if u['is_banned']: return
        now = time.time()
        if now - u['last_peas'] < 300:
            left = int(300 - (now - u['last_peas']))
            await update.message.reply_text(f"Подождите {left} сек.")
            return
        all_cards = get_all_cards()
        if not all_cards:
            await update.message.reply_text("Пул карт пуст. Ожидайте добавления карт администрацией.")
            return
        update_user(uid, 'last_peas', now)
        msg = await update.message.reply_text(f"Открываем попытку… {gen_spin()}")
        for _ in range(3):
            await asyncio.sleep(0.35)
            try: await msg.edit_text(f"Определяем результат… {gen_spin()}")
            except: pass
        kb = [[InlineKeyboardButton("Получить результат", callback_data=f"spin_free:{uid}")]]
        try:
            await msg.edit_text(f"Попытка готова.\nШанс: {u['chance']}%", reply_markup=InlineKeyboardMarkup(kb))
        except: pass
        return

    state = user_states.get(uid)
    if not state: return
    action = state.get('action')

    if action == 'support_msg':
        with db_lock:
            conn = db()
            try:
                conn.execute(
                    "INSERT INTO support_tickets (user_id,user_message,created_at) VALUES (?,?,?)",
                    (uid, text, time.time())
                )
                ticket_id = conn.execute("SELECT last_insert_rowid()")[0]
                conn.commit()
            finally:
                conn.close()
        user_states.pop(uid, None)
        await update.message.reply_text("Сообщение отправлено в техподдержку.")
        
        admin = get_user_by_username(ADMIN_USERNAME)
        if admin:
            uname = f"@{u['username']}" if u['username'] else u['name']
            kb = [[InlineKeyboardButton("Ответить", callback_data=f"sup_reply:{admin['user_id']}:{ticket_id}")]]
            try:
                await ctx.bot.send_message(
                    admin['user_id'],
                    f"Заявка #{ticket_id} от {uname}:\n\n{text}",
                    reply_markup=InlineKeyboardMarkup(kb)
                )
            except: pass
        return

    if action == 'admin_reply':
        ticket_id = state['ticket_id']
        with db_lock:
            conn = db()
            try:
                ticket = conn.execute("SELECT * FROM support_tickets WHERE id=?", (ticket_id,)).fetchone()
                if ticket:
                    conn.execute(
                        "UPDATE support_tickets SET admin_response=?, status='answered' WHERE id=?",
                        (text, ticket_id)
                    )
                    conn.commit()
            finally:
                conn.close()
        user_states.pop(uid, None)
        await update.message.reply_text("Ответ отправлен пользователю.")
        
        if ticket:
            try:
                kb = [[InlineKeyboardButton("Ответить", callback_data=f"sup_answer:{ticket['user_id']}:{ticket_id}")]]
                await ctx.bot.send_message(
                    ticket['user_id'],
                    f"Ответ от техподдержки:\n\n{text}",
                    reply_markup=InlineKeyboardMarkup(kb)
                )
            except: pass
        return

    if action == 'user_reply_to_admin':
        ticket_id = state['ticket_id']
        with db_lock:
            conn = db()
            try:
                conn.execute(
                    "INSERT INTO support_tickets (user_id,user_message,created_at) VALUES (?,?,?)",
                    (uid, f"[Ответ на заявку #{ticket_id}] {text}", time.time())
                )
                new_ticket_id = conn.execute("SELECT last_insert_rowid()")[0]
                conn.commit()
            finally:
                conn.close()
        user_states.pop(uid, None)
        await update.message.reply_text("Ответ отправлен.")
        
        admin = get_user_by_username(ADMIN_USERNAME)
        if admin:
            uname = f"@{u['username']}" if u['username'] else u['name']
            kb = [[InlineKeyboardButton("Ответить", callback_data=f"sup_reply:{admin['user_id']}:{new_ticket_id}")]]
            try:
                await ctx.bot.send_message(
                    admin['user_id'],
                    f"Ответ на заявку #{ticket_id} от {uname}:\n\n{text}",
                    reply_markup=InlineKeyboardMarkup(kb)
                )
            except: pass
        return

    if action == 'rest_name':
        user_states[uid] = {'action': 'rest_photo', 'card_name': text}
        await update.message.reply_text("Пришлите изображение карты:")
        return

    if action == 'addcard_name':
        user_states[uid] = {'action': 'addcard_photo', 'card_name': text}
        await update.message.reply_text("Пришлите изображение карты:")
        return

    if action == 'addcard_excl':
        try:
            limit = int(text)
            if limit < 0: limit = 0
            with db_lock:
                conn = db()
                try:
                    conn.execute(
                        "INSERT INTO cards (name,photo_file_id,author,excl_limit) VALUES (?,?,?,?)",
                        (state['card_name'], state['photo_id'], 'PeasCards', limit)
                    )
                    conn.commit()
                finally:
                    conn.close()
            user_states.pop(uid, None)
            excl_txt = f"Эксклюзивных копий: {limit}" if limit > 0 else "Без эксклюзива"
            await update.message.reply_text(f"Карта «{state['card_name']}» добавлена.\n{excl_txt}")
            
            cards = get_user_cards(uid)
            if len(cards) == 1 and not u['farm_enabled']:
                await update.message.reply_text("Теперь вы можете включить авто ферму. Напишите /farm")
        except ValueError:
            await update.message.reply_text("Введите число (0 = без эксклюзива).")
        return

    if action == 'admin_excl_limit':
        try:
            limit = int(text)
            if limit < 0: limit = 0
            req_id = state['req_id']
            with db_lock:
                conn = db()
                try:
                    req = conn.execute("SELECT * FROM rest_requests WHERE id=?", (req_id,)).fetchone()
                    if req and req['status'] == 'pending':
                        conn.execute(
                            "INSERT INTO cards (name,photo_file_id,author,excl_limit) VALUES (?,?,?,?)",
                            (req['card_name'], req['photo_file_id'], 'PeasCards', limit)
                        )
                        conn.execute("UPDATE rest_requests SET status='accepted' WHERE id=?", (req_id,))
                        conn.commit()
                finally:
                    conn.close()
            user_states.pop(uid, None)
            excl_txt = f"Эксклюзивных копий: {limit}" if limit > 0 else "Без эксклюзива"
            await update.message.reply_text(f"Карта «{req['card_name']}» добавлена.\n{excl_txt}")
            try: await ctx.bot.send_message(req['user_id'], f"Ваша карта «{req['card_name']}» принята!")
            except: pass
            
            target_cards = get_user_cards(req['user_id'])
            if len(target_cards) == 1:
                try: await ctx.bot.send_message(req['user_id'], "Теперь вы можете включить авто ферму. Напишите /farm")
                except: pass
        except ValueError:
            await update.message.reply_text("Введите число.")
        return

    if action == 'extlimit_amount':
        try:
            amount = int(text)
            if amount < 0:
                await update.message.reply_text("Число должно быть положительным.")
                return
            card_id = state['card_id']
            with db_lock:
                conn = db()
                try:
                    conn.execute("UPDATE cards SET excl_limit=excl_limit+? WHERE id=?", (amount, card_id))
                    conn.commit()
                    card = conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
                finally:
                    conn.close()
            user_states.pop(uid, None)
            remaining = card['excl_limit'] - card['excl_count']
            await update.message.reply_text(
                f"Лимит продлен.\nКарта: {card['name']}\nНовый лимит: {card['excl_limit']}\nОсталось: {remaining}"
            )
        except ValueError:
            await update.message.reply_text("Введите число.")
        return

    if action == 'give_username':
        username = parse_username(text)
        if not username:
            await update.message.reply_text("Введите @username")
            return
        target = get_user_by_username(username)
        if not target:
            await update.message.reply_text("Пользователь не найден.")
            user_states.pop(uid, None)
            return
        user_states[uid]['target_id'] = target['user_id']
        user_states[uid]['action'] = 'give_amount'
        await update.message.reply_text(f"Введите количество карт для @{username}:")
        return

    if action == 'give_amount':
        try:
            qty = int(text)
            if qty < 1:
                await update.message.reply_text("Минимум 1.")
                return
            card_id = state['card_id']
            rarity = state['rarity']
            target_id = state['target_id']
            add_user_card(target_id, card_id, rarity, qty)
            sync_to_firebase()
            user_states.pop(uid, None)
            with db_lock:
                conn = db()
                try:
                    card = conn.execute("SELECT name FROM cards WHERE id=?", (card_id,)).fetchone()
                finally:
                    conn.close()
            ri = RARITIES[rarity]
            target = get_user(target_id)
            await update.message.reply_text(
                f"Выдано {qty} карт «{card['name']}» {ri['e']} пользователю {target['name']}"
            )
            try:
                await ctx.bot.send_message(target_id, f"Админ выдал вам {qty} карт «{card['name']}» {ri['e']}")
            except: pass
        except ValueError:
            await update.message.reply_text("Введите число.")
        return

    if action == 'ungive_username':
        username = parse_username(text)
        if not username:
            await update.message.reply_text("Введите @username")
            return
        target = get_user_by_username(username)
        if not target:
            await update.message.reply_text("Пользователь не найден.")
            user_states.pop(uid, None)
            return
        user_states[uid]['target_id'] = target['user_id']
        user_states[uid]['action'] = 'ungive_amount'
        await update.message.reply_text(f"Введите количество карт для изъятия у @{username}:")
        return

    if action == 'ungive_amount':
        try:
            qty = int(text)
            if qty < 1:
                await update.message.reply_text("Минимум 1.")
                return
            card_id = state['card_id']
            rarity = state['rarity']
            target_id = state['target_id']
            success = remove_user_card(target_id, card_id, rarity, qty)
            if not success:
                await update.message.reply_text("Недостаточно карт у пользователя.")
                user_states.pop(uid, None)
                return
            sync_to_firebase()
            user_states.pop(uid, None)
            with db_lock:
                conn = db()
                try:
                    card = conn.execute("SELECT name FROM cards WHERE id=?", (card_id,)).fetchone()
                finally:
                    conn.close()
            ri = RARITIES[rarity]
            target = get_user(target_id)
            await update.message.reply_text(
                f"Изъято {qty} карт «{card['name']}» {ri['e']} у {target['name']}"
            )
        except ValueError:
            await update.message.reply_text("Введите число.")
        return

    if action == 'mkt_exchange_input':
        if text.lower() in ['отмена', 'назад']:
            user_states.pop(uid, None)
            await update.message.reply_text("Отменено.")
            return
        try:
            amount = int(text)
            if amount < 100:
                await update.message.reply_text("Минимум 100 монет.")
                return
            if amount > u['coins']:
                await update.message.reply_text("Недостаточно монет.")
                return
            diamonds = int(amount / 10)
            kb = [
                [InlineKeyboardButton("Обменять", callback_data=f"exch_do:{uid}:{amount}:{diamonds}")],
                [InlineKeyboardButton("Отмена", callback_data=f"exch_cancel:{uid}")]
            ]
            user_states.pop(uid, None)
            await update.message.reply_text(f"Обменять {amount}$ на {diamonds} алмазов?", reply_markup=InlineKeyboardMarkup(kb))
        except ValueError:
            await update.message.reply_text("Введите число.")
        return

    if action == 'sell_qty':
        try:
            qty = int(text)
            if qty < 1:
                await update.message.reply_text("Минимум 1.")
                return
            with db_lock:
                conn = db()
                try:
                    uc = conn.execute(
                        "SELECT quantity FROM user_cards WHERE user_id=? AND card_id=? AND rarity=?",
                        (uid, state['card_id'], state['rarity'])
                    ).fetchone()
                finally:
                    conn.close()
            if not uc or uc['quantity'] < qty:
                await update.message.reply_text("Недостаточно карт.")
                return
            user_states[uid] = {'action': 'sell_title', 'card_id': state['card_id'], 'rarity': state['rarity'], 'qty': qty}
            await update.message.reply_text("Введите название объявления:")
        except ValueError:
            await update.message.reply_text("Введите число.")
        return

    if action == 'sell_title':
        user_states[uid]['title'] = text
        user_states[uid]['action'] = 'sell_price'
        await update.message.reply_text("Цена за одну карту в алмазах (минимум 10):")
        return

    if action == 'sell_price':
        try:
            price = int(text)
            if price < 10:
                await update.message.reply_text("Минимум 10 алмазов.")
                return
            s = user_states[uid]
            earnings = int(price * 10 * 0.9 * s['qty'])
            title_enc = s['title'].replace(':', ';')
            kb = [
                [InlineKeyboardButton("Подтвердить", callback_data=f"sell_do:{uid}:{s['card_id']}:{s['rarity']}:{s['qty']}:{price}:{title_enc}")],
                [InlineKeyboardButton("Отмена", callback_data=f"sell_cancel:{uid}")]
            ]
            user_states.pop(uid, None)
            await update.message.reply_text(f"Ваша выручка: {earnings}$. Подтвердить?", reply_markup=InlineKeyboardMarkup(kb))
        except ValueError:
            await update.message.reply_text("Введите число.")
        return

# === ОБРАБОТКА ФОТО ===
async def handle_photo(update: Update, ctx):
    uid = update.effective_user.id
    state = user_states.get(uid)
    if not state: return
    photo = update.message.photo[-1]

    if state.get('action') == 'rest_photo':
        user_states[uid] = {'action': 'rest_confirm', 'card_name': state['card_name'], 'photo_id': photo.file_id}
        kb = [
            [InlineKeyboardButton("Отправить", callback_data=f"rest_send:{uid}")],
            [InlineKeyboardButton("Отмена", callback_data=f"rest_cancel:{uid}")]
        ]
        await update.message.reply_photo(photo=photo.file_id,
            caption=f"Карта: {state['card_name']}\nПодтвердить отправку?",
            reply_markup=InlineKeyboardMarkup(kb))
        return

    if state.get('action') == 'addcard_photo':
        user_states[uid] = {'action': 'addcard_excl', 'card_name': state['card_name'], 'photo_id': photo.file_id}
        await update.message.reply_text("Сколько эксклюзивных копий? (0 = без эксклюзива):")
        return

# === КНОПКИ ===
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

    if action == "spin_free":
        u = get_user(uid)
        card = pick_card()
        if not card:
            await safe_del(query.message)
            await query.message.chat.send_message("Пул карт пуст.")
            return
        rarity = roll_rarity(u['chance'], card)

        if rarity in ['Обычный', 'Редкий']:
            new_chance = min(u['chance'] + 2.5, 100)
        else:
            new_chance = 10.0
        update_user(uid, 'chance', new_chance)

        r_info = RARITIES[rarity]
        if card['excl_limit'] > 0:
            remaining = f"{card['excl_limit'] - card['excl_count']}"
        else:
            remaining = "Неограниченно"

        kb = [[InlineKeyboardButton("Получить", callback_data=f"get_card:{uid}:{card['id']}:{rarity}")]]
        if rarity != 'Эксклюзивный':
            kb[0].append(InlineKeyboardButton(f"Продать за {r_info['price']}$", callback_data=f"sell_drop:{uid}:{card['id']}:{rarity}"))

        text = (f"{card['name'].upper()}\n{rarity.upper()} {r_info['e']}\n\n"
                f"Осталось: {remaining}\nАвтор: {card['author']}")
        await safe_del(query.message)
        if card['photo_file_id']:
            await query.message.chat.send_photo(photo=card['photo_file_id'], caption=text, reply_markup=InlineKeyboardMarkup(kb))
        else:
            await query.message.chat.send_message(text, reply_markup=InlineKeyboardMarkup(kb))
        return

    if action == "shop_epic":
        u = get_user(uid)
        now = time.time()
        if now - u['last_epic_peas'] < 86400:
            left = int((86400 - (now - u['last_epic_peas'])) / 3600) + 1
            await query.answer(f"Эпический горошек доступен раз в 24 часа. Попробуйте через {left} час.", show_alert=True)
            return
        if u['coins'] < 1000:
            await query.answer("Недостаточно монет. Нужно 1000$.", show_alert=True)
            return
        
        update_user(uid, 'last_epic_peas', now)
        update_user(uid, 'coins', u['coins'] - 1000)
        
        msg = await query.message.chat.send_message(f"Открываем эпический горошек… {gen_spin()}")
        for _ in range(5):
            await asyncio.sleep(0.5)
            try: await msg.edit_text(f"Определяем результат… {gen_spin()}")
            except: pass
        
        kb = [[InlineKeyboardButton("Получить результат", callback_data=f"epic_spin:{uid}")]]
        try:
            await msg.edit_text("Эпический горошек готов.\nВы получите 3 случайные карты.", reply_markup=InlineKeyboardMarkup(kb))
        except: pass
        return

    if action == "epic_spin":
        u = get_user(uid)
        cards = pick_three_cards()
        if not cards:
            await safe_del(query.message)
            await query.message.chat.send_message("Пул карт пуст.")
            return
        
        results = []
        media_group = []
        
        for card in cards:
            rarity = roll_rarity(u['chance'], card)
            ri = RARITIES[rarity]
            
            card_result = claim_card(uid, card['id'], rarity)
            if card_result:
                results.append(f"{card['name']} — {rarity} {ri['e']}")
            
            if card['photo_file_id']:
                media_group.append(InputMediaPhoto(media=card['photo_file_id'], caption=f"{card['name']}\n{rarity} {ri['e']}"))
            else:
                results.append(f"{card['name']} — {rarity} {ri['e']} (без фото)")
        
        await safe_del(query.message)
        
        if media_group:
            await query.message.chat.send_media_group(media=media_group)
        
        if results:
            result_text = "Ваш эпический горошек:\n\n" + "\n".join(results)
            await query.message.chat.send_message(result_text)
        else:
            await query.message.chat.send_message("Эпический горошек получен. Проверьте инвентарь.")
        
        sync_to_firebase()
        return

    if action == "get_card":
        card_id, rarity = int(parts[2]), parts[3]
        card = claim_card(uid, card_id, rarity)
        if not card:
            await query.answer("Карта уже недоступна.", show_alert=True)
            return
        await safe_del(query.message)
        await query.message.chat.send_message(f"Карта добавлена в коллекцию.\n{card['name']} — {RARITIES[rarity]['e']} {rarity}")
        
        cards = get_user_cards(uid)
        if len(cards) == 1:
            u = get_user(uid)
            if not u['farm_enabled']:
                await query.message.chat.send_message("Теперь вы можете включить авто ферму. Напишите /farm")
        
        sync_to_firebase()
        return

    if action == "sell_drop":
        card_id, rarity = int(parts[2]), parts[3]
        price = RARITIES[rarity]['price']
        add_coins(uid, price)
        sync_to_firebase()
        await safe_del(query.message)
        await query.message.chat.send_message(f"Продано за {price}$")
        return

    if action == "inv_item":
        card_id, rarity = int(parts[2]), parts[3]
        with db_lock:
            conn = db()
            try:
                card = conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
                uc = conn.execute(
                    "SELECT quantity FROM user_cards WHERE user_id=? AND card_id=? AND rarity=?",
                    (uid, card_id, rarity)
                ).fetchone()
            finally:
                conn.close()
        if not card or not uc: return
        ri = RARITIES[rarity]
        price = ri['price']
        
        kb = [[InlineKeyboardButton(f"Продать 1 за {price}$", callback_data=f"inv_sell1:{uid}:{card_id}:{rarity}")]]
        if uc['quantity'] > 1:
            total_price = price * uc['quantity']
            kb.append([InlineKeyboardButton(f"Продать все ({uc['quantity']}) за {total_price}$", callback_data=f"inv_sellall:{uid}:{card_id}:{rarity}")])
        if rarity != 'Эксклюзивный' and rarity in UPGRADE_PATH:
            upgrade_cost = UPGRADE_COSTS[rarity]
            next_rarity = UPGRADE_PATH[rarity]
            kb.append([InlineKeyboardButton(f"Улучшить до {next_rarity} ({upgrade_cost}$)", callback_data=f"upgrade:{uid}:{card_id}:{rarity}")])
        kb.append([InlineKeyboardButton("Назад", callback_data=f"inv_back:{uid}")])
        
        txt = f"{card['name']} {ri['e']} {rarity}\nКоличество: {uc['quantity']}"
        await safe_del(query.message)
        if card['photo_file_id']:
            await query.message.chat.send_photo(photo=card['photo_file_id'], caption=txt, reply_markup=InlineKeyboardMarkup(kb))
        else:
            await query.message.chat.send_message(txt, reply_markup=InlineKeyboardMarkup(kb))
        return

    if action == "inv_sell1":
        card_id, rarity = int(parts[2]), parts[3]
        if not remove_user_card(uid, card_id, rarity, 1):
            await query.answer("Карта уже продана.", show_alert=True)
            return
        price = RARITIES[rarity]['price']
        add_coins(uid, price)
        sync_to_firebase()
        await safe_del(query.message)
        await query.message.chat.send_message(f"Продано за {price}$")
        return

    if action == "inv_sellall":
        card_id, rarity = int(parts[2]), parts[3]
        with db_lock:
            conn = db()
            try:
                uc = conn.execute("SELECT quantity FROM user_cards WHERE user_id=? AND card_id=? AND rarity=?", (uid, card_id, rarity)).fetchone()
            finally:
                conn.close()
        if not uc:
            await query.answer("Карты уже проданы.", show_alert=True)
            return
        qty = uc['quantity']
        for _ in range(qty):
            remove_user_card(uid, card_id, rarity, 1)
        price = RARITIES[rarity]['price'] * qty
        add_coins(uid, price)
        sync_to_firebase()
        await safe_del(query.message)
        await query.message.chat.send_message(f"Продано {qty} карт за {price}$")
        return

    if action == "upgrade":
        card_id, from_rarity = int(parts[2]), parts[3]
        success, new_rarity, error_msg = upgrade_card(uid, card_id, from_rarity)
        if success:
            sync_to_firebase()
            await safe_del(query.message)
            await query.message.chat.send_message(f"Карта успешно улучшена до редкости {new_rarity} {RARITIES[new_rarity]['e']}")
        else:
            await query.answer(error_msg or "Ошибка улучшения", show_alert=True)
        return

    if action == "inv_back":
        cards = get_user_cards(uid)
        if not cards:
            await safe_del(query.message)
            await query.message.chat.send_message("Инвентарь пуст.")
            return
        rarity_order = {'Эксклюзивный': 0, 'Легендарный': 1, 'Мифический': 2, 'Эпический': 3, 'Редкий': 4, 'Обычный': 5}
        cards_sorted = sorted(cards, key=lambda c: rarity_order.get(c['rarity'], 99))
        kb = []
        for c in cards_sorted:
            ri = RARITIES[c['rarity']]
            btn_text = f"{c['card_name']} {ri['e']}, {c['quantity']}"
            kb.append([InlineKeyboardButton(btn_text, callback_data=f"inv_item:{uid}:{c['card_id']}:{c['rarity']}")])
        kb.append([InlineKeyboardButton("Закрыть", callback_data=f"noop")])
        await safe_del(query.message)
        await query.message.chat.send_message("Инвентарь", reply_markup=InlineKeyboardMarkup(kb))
        return

    if action == "rest_send":
        state = user_states.get(uid)
        if not state: return
        with db_lock:
            conn = db()
            try:
                conn.execute("INSERT INTO rest_requests (user_id,card_name,photo_file_id) VALUES (?,?,?)", (uid, state['card_name'], state['photo_id']))
                req_id = conn.execute("SELECT last_insert_rowid()")[0]
                conn.commit()
            finally:
                conn.close()
        user_states.pop(uid, None)
        await safe_del(query.message)
        await query.message.chat.send_message("Заявка отправлена на модерацию.")

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
                if state['photo_id']:
                    await ctx.bot.send_photo(admin['user_id'], photo=state['photo_id'], caption=caption, reply_markup=InlineKeyboardMarkup(kb))
                else:
                    await ctx.bot.send_message(admin['user_id'], caption, reply_markup=InlineKeyboardMarkup(kb))
            except: pass
        return

    if action == "rest_cancel":
        user_states.pop(uid, None)
        await safe_del(query.message)
        await query.message.chat.send_message("Отменено.")
        return

    if action == "admin_acc":
        if not is_admin(query.from_user.id): return
        req_id = int(parts[2])
        user_states[query.from_user.id] = {'action': 'admin_excl_limit', 'req_id': req_id}
        await safe_del(query.message)
        await query.message.chat.send_message("Сколько эксклюзивных копий? (0 = без эксклюзива):")
        return

    if action == "admin_rej":
        if not is_admin(query.from_user.id): return
        req_id = int(parts[2])
        with db_lock:
            conn = db()
            try:
                req = conn.execute("SELECT * FROM rest_requests WHERE id=?", (req_id,)).fetchone()
                if req:
                    conn.execute("UPDATE rest_requests SET status='rejected' WHERE id=?", (req_id,))
                    conn.commit()
            finally:
                conn.close()
        if req:
            try: await ctx.bot.send_message(req['user_id'], f"Карта «{req['card_name']}» отклонена.")
            except: pass
        await safe_del(query.message)
        await query.message.chat.send_message("Отклонено.")
        return

    if action == "admin_ban":
        if not is_admin(query.from_user.id): return
        req_id = int(parts[2])
        with db_lock:
            conn = db()
            try:
                req = conn.execute("SELECT * FROM rest_requests WHERE id=?", (req_id,)).fetchone()
                if req:
                    conn.execute("UPDATE users SET is_banned=1 WHERE user_id=?", (req['user_id'],))
                    conn.execute("UPDATE rest_requests SET status='banned' WHERE id=?", (req_id,))
                    conn.commit()
            finally:
                conn.close()
        if req:
            try: await ctx.bot.send_message(req['user_id'], "Ваш доступ ограничен.")
            except: pass
        await safe_del(query.message)
        await query.message.chat.send_message("Пользователь заблокирован.")
        return

    if action == "extl_pick":
        card_id = int(parts[2])
        user_states[uid] = {'action': 'extlimit_amount', 'card_id': card_id}
        with db_lock:
            conn = db()
            try:
                card = conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
            finally:
                conn.close()
        await safe_del(query.message)
        remaining = card['excl_limit'] - card['excl_count']
        await query.message.chat.send_message(f"Карта: {card['name']}\nТекущий лимит: {card['excl_limit']}\nОсталось: {remaining}\n\nСколько добавить эксклюзивных копий?")
        return

    if action == "give_pick":
        card_id = int(parts[2])
        user_states[uid] = {'action': 'give_rarity', 'card_id': card_id}
        kb = [[InlineKeyboardButton(f"{ri['e']} {rn}", callback_data=f"give_rar:{uid}:{card_id}:{rn}")] for rn, ri in RARITIES.items()]
        kb.append([InlineKeyboardButton("Отмена", callback_data=f"noop")])
        await safe_del(query.message)
        await query.message.chat.send_message("Выберите редкость:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if action == "give_rar":
        card_id, rarity = int(parts[2]), parts[3]
        user_states[uid] = {'action': 'give_username', 'card_id': card_id, 'rarity': rarity}
        await safe_del(query.message)
        await query.message.chat.send_message("Введите @username получателя:")
        return

    if action == "ungive_pick":
        card_id = int(parts[2])
        user_states[uid] = {'action': 'ungive_rarity', 'card_id': card_id}
        kb = [[InlineKeyboardButton(f"{ri['e']} {rn}", callback_data=f"ungive_rar:{uid}:{card_id}:{rn}")] for rn, ri in RARITIES.items()]
        kb.append([InlineKeyboardButton("Отмена", callback_data=f"noop")])
        await safe_del(query.message)
        await query.message.chat.send_message("Выберите редкость:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if action == "ungive_rar":
        card_id, rarity = int(parts[2]), parts[3]
        user_states[uid] = {'action': 'ungive_username', 'card_id': card_id, 'rarity': rarity}
        await safe_del(query.message)
        await query.message.chat.send_message("Введите @username у кого изъять:")
        return

    if action == "shop_chance":
        kb = [
            [InlineKeyboardButton("1% — 100$", callback_data=f"shop_buy:{uid}:100:1")],
            [InlineKeyboardButton("2.5% — 200$", callback_data=f"shop_buy:{uid}:200:2.5")],
            [InlineKeyboardButton("15% — 500$", callback_data=f"shop_buy:{uid}:500:15")],
            [InlineKeyboardButton("30% — 1000$", callback_data=f"shop_buy:{uid}:1000:30")],
            [InlineKeyboardButton("100% — 5000$", callback_data=f"shop_buy:{uid}:5000:100")],
            [InlineKeyboardButton("Назад", callback_data=f"shop_back:{uid}")],
        ]
        u = get_user(uid)
        await safe_del(query.message)
        await query.message.chat.send_message(f"Улучшение шанса.\nБаланс: {u['coins']}$. Текущий шанс: {u['chance']}%", reply_markup=InlineKeyboardMarkup(kb))
        return

    if action == "shop_exchange":
        user_states[uid] = {'action': 'mkt_exchange_input'}
        await safe_del(query.message)
        await query.message.chat.send_message("Сколько монет обменять? (100$ = 10 алмазов, мин 100$):")
        return

    if action == "shop_back":
        kb = [
            [InlineKeyboardButton("Улучшение шанса", callback_data=f"shop_chance:{uid}")],
            [InlineKeyboardButton("Обмен валюты", callback_data=f"shop_exchange:{uid}")],
            [InlineKeyboardButton("Эпический горошек (1000$)", callback_data=f"shop_epic:{uid}")],
        ]
        u = get_user(uid)
        await safe_del(query.message)
        await query.message.chat.send_message(f"Магазин.\nБаланс: {u['coins']}$. Алмазы: {u['diamonds']}\nШанс: {u['chance']}%", reply_markup=InlineKeyboardMarkup(kb))
        return

    if action == "shop_buy":
        cost, bonus = int(parts[2]), float(parts[3])
        u = get_user(uid)
        if u['coins'] < cost:
            await query.answer("Недостаточно монет.", show_alert=True)
            return
        with db_lock:
            conn = db()
            try:
                conn.execute("UPDATE users SET coins=coins-?, chance=MIN(chance+?,100) WHERE user_id=?", (cost, bonus, uid))
                conn.commit()
            finally:
                conn.close()
        sync_to_firebase()
        u = get_user(uid)
        await safe_del(query.message)
        await query.message.chat.send_message(f"Шанс обновлен: {u['chance']}%. Баланс: {u['coins']}$")
        return

    if action == "mkt_exchange":
        user_states[uid] = {'action': 'mkt_exchange_input'}
        await safe_del(query.message)
        await query.message.chat.send_message("Сколько монет обменять? (100$ = 10 алмазов, мин 100$):")
        return

    if action == "exch_do":
        amount, diamonds = int(parts[2]), int(parts[3])
        u = get_user(uid)
        if u['coins'] < amount:
            await query.answer("Недостаточно монет.", show_alert=True)
            return
        with db_lock:
            conn = db()
            try:
                conn.execute("UPDATE users SET coins=coins-?, diamonds=diamonds+? WHERE user_id=?", (amount, diamonds, uid))
                conn.commit()
            finally:
                conn.close()
        sync_to_firebase()
        await safe_del(query.message)
        await query.message.chat.send_message(f"Обмен выполнен. Получено {diamonds} алмазов.")
        return

    if action == "exch_cancel":
        await safe_del(query.message)
        await query.message.chat.send_message("Отменено.")
        return

    if action == "mkt_new":
        page = int(parts[2]) if len(parts) > 2 else 0
        per_page = 10
        offset = page * per_page
        
        with db_lock:
            conn = db()
            try:
                total = conn.execute("SELECT COUNT(*) FROM market_listings").fetchone()[0]
                listings = conn.execute(
                    "SELECT ml.*, u.name as sn, u.username as su, c.name as cn, c.photo_file_id "
                    "FROM market_listings ml JOIN users u ON ml.seller_id=u.user_id JOIN cards c ON ml.card_id=c.id "
                    "ORDER BY ml.price_diamonds ASC LIMIT ? OFFSET ?", (per_page, offset)
                ).fetchall()
            finally:
                conn.close()
        
        if not listings and page == 0:
            await safe_del(query.message)
            await query.message.chat.send_message("Рынок пуст.")
            return
        
        kb = []
        for l in listings:
            ri = RARITIES[l['rarity']]
            uname = f"@{l['su']}" if l['su'] else l['sn']
            btn_text = f"{l['cn']} {ri['e']} • {l['quantity']} алм. {l['price_diamonds']} | {uname}"
            kb.append([InlineKeyboardButton(btn_text, callback_data=f"mkt_item:{uid}:{l['id']}")])
        
        nav = []
        if page > 0: nav.append(InlineKeyboardButton("Назад", callback_data=f"mkt_new:{uid}:{page-1}"))
        if offset + per_page < total: nav.append(InlineKeyboardButton("Далее", callback_data=f"mkt_new:{uid}:{page+1}"))
        if nav: kb.append(nav)
        kb.append([InlineKeyboardButton("Назад в рынок", callback_data=f"mkt_back:{uid}")])
        
        await safe_del(query.message)
        await query.message.chat.send_message(f"Рынок. Страница {page+1}.\nСамые выгодные предложения:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if action == "mkt_item":
        lid = int(parts[2])
        with db_lock:
            conn = db()
            try:
                l = conn.execute("SELECT ml.*, u.name as sn, u.username as su, c.name as cn, c.photo_file_id FROM market_listings ml JOIN users u ON ml.seller_id=u.user_id JOIN cards c ON ml.card_id=c.id WHERE ml.id=?", (lid,)).fetchone()
            finally:
                conn.close()
        if not l:
            await query.answer("Уже недоступно.", show_alert=True)
            return
        ri = RARITIES[l['rarity']]
        uname = f"@{l['su']}" if l['su'] else l['sn']
        txt = f"Товар #{l['id']}\nНазвание: {l['title']}\nКарта: {l['cn']} {ri['e']}\nПродавец: {l['sn']} ({uname})\nКол-во: {l['quantity']}\nЦена: {l['price_diamonds']} алмазов"
        kb = [
            [InlineKeyboardButton(f"Купить за {l['price_diamonds']} алмазов", callback_data=f"mkt_buy:{uid}:{lid}")],
            [InlineKeyboardButton("Назад", callback_data=f"mkt_new:{uid}:0")]
        ]
        await safe_del(query.message)
        if l['photo_file_id']:
            await query.message.chat.send_photo(photo=l['photo_file_id'], caption=txt, reply_markup=InlineKeyboardMarkup(kb))
        else:
            await query.message.chat.send_message(txt, reply_markup=InlineKeyboardMarkup(kb))
        return

    if action == "mkt_buy":
        lid = int(parts[2])
        with db_lock:
            conn = db()
            try:
                l = conn.execute("SELECT * FROM market_listings WHERE id=?", (lid,)).fetchone()
            finally:
                conn.close()
        if not l:
            await query.answer("Уже недоступно.", show_alert=True)
            return
        u = get_user(uid)
        if u['diamonds'] < l['price_diamonds']:
            await query.answer("Недостаточно алмазов!", show_alert=True)
            return
        if l['seller_id'] == uid:
            await query.answer("Нельзя покупать у себя!", show_alert=True)
            return
        earnings = int(l['price_diamonds'] * 10 * 0.9)
        with db_lock:
            conn = db()
            try:
                conn.execute("UPDATE users SET diamonds=diamonds-? WHERE user_id=?", (l['price_diamonds'], uid))
                conn.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (earnings, l['seller_id']))
                conn.execute("DELETE FROM market_listings WHERE id=?", (lid,))
                conn.commit()
            finally:
                conn.close()
        add_user_card(uid, l['card_id'], l['rarity'], l['quantity'])
        sync_to_firebase()
        await safe_del(query.message)
        await query.message.chat.send_message(f"Покупка завершена. Списано {l['price_diamonds']} алмазов.")
        try: await ctx.bot.send_message(l['seller_id'], f"Ваша карта продана. Начислено {earnings}$")
        except: pass
        return

    if action == "mkt_cards":
        cards = get_all_cards()
        if not cards:
            await safe_del(query.message)
            await query.message.chat.send_message("Рынок пуст.")
            return
        kb = [[InlineKeyboardButton(c['name'], callback_data=f"mkt_card:{uid}:{c['id']}")] for c in cards]
        kb.append([InlineKeyboardButton("Назад", callback_data=f"mkt_back:{uid}")])
        await safe_del(query.message)
        await query.message.chat.send_message("Выберите карту:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if action == "mkt_card":
        card_id = int(parts[2])
        with db_lock:
            conn = db()
            try:
                card = conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
            finally:
                conn.close()
        if not card: return
        kb = []
        for rn, ri in RARITIES.items():
            with db_lock:
                conn = db()
                try:
                    cnt = conn.execute("SELECT COUNT(*) FROM market_listings WHERE card_id=? AND rarity=?", (card_id, rn)).fetchone()[0]
                finally:
                    conn.close()
            if cnt > 0:
                kb.append([InlineKeyboardButton(f"{ri['e']} {rn} ({cnt})", callback_data=f"mkt_rarity:{uid}:{card_id}:{rn}")])
        if not kb:
            kb.append([InlineKeyboardButton("Нет предложений", callback_data="noop")])
        kb.append([InlineKeyboardButton("Назад", callback_data=f"mkt_cards:{uid}")])
        await safe_del(query.message)
        await query.message.chat.send_message(f"Редкости карты «{card['name']}»:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if action == "mkt_rarity":
        card_id, rarity = int(parts[2]), parts[3]
        with db_lock:
            conn = db()
            try:
                listings = conn.execute("SELECT ml.*, u.name as sn, u.username as su FROM market_listings ml JOIN users u ON ml.seller_id=u.user_id WHERE ml.card_id=? AND ml.rarity=? ORDER BY ml.price_diamonds", (card_id, rarity)).fetchall()
                card = conn.execute("SELECT name FROM cards WHERE id=?", (card_id,)).fetchone()
            finally:
                conn.close()
        ri = RARITIES[rarity]
        kb = []
        for l in listings:
            kb.append([InlineKeyboardButton(f"{card['name']} {ri['e']}, {l['quantity']} шт. {l['price_diamonds']} алм.", callback_data=f"mkt_item:{uid}:{l['id']}")])
        if not kb:
            kb.append([InlineKeyboardButton("Нет предложений", callback_data="noop")])
        kb.append([InlineKeyboardButton("Назад", callback_data=f"mkt_card:{uid}:{card_id}")])
        await safe_del(query.message)
        await query.message.chat.send_message("Предложения:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if action == "mkt_back":
        with db_lock:
            conn = db()
            try:
                total = conn.execute("SELECT COUNT(*) FROM market_listings").fetchone()[0]
                my = conn.execute("SELECT COUNT(*) FROM market_listings WHERE seller_id=?", (uid,)).fetchone()[0]
            finally:
                conn.close()
        kb = [
            [InlineKeyboardButton("Рынок", callback_data=f"mkt_new:{uid}:0")],
            [InlineKeyboardButton("Рынок по категориям", callback_data=f"mkt_cards:{uid}")],
            [InlineKeyboardButton("Обмен валюты", callback_data=f"mkt_exchange:{uid}")],
        ]
        await safe_del(query.message)
        await query.message.chat.send_message(f"Рынок. Всего: {total} | Ваших: {my}", reply_markup=InlineKeyboardMarkup(kb))
        return

    if action == "sell_pick":
        card_id = int(parts[2])
        with db_lock:
            conn = db()
            try:
                rows = conn.execute("SELECT rarity,quantity FROM user_cards WHERE user_id=? AND card_id=? AND quantity>0", (uid, card_id)).fetchall()
            finally:
                conn.close()
        kb = []
        for r in rows:
            if r['rarity'] != 'Эксклюзивный':
                ri = RARITIES.get(r['rarity'], {})
                kb.append([InlineKeyboardButton(f"{ri.get('e','')} {r['rarity']} ({r['quantity']} шт.)", callback_data=f"sell_rarity:{uid}:{card_id}:{r['rarity']}")])
        if not kb:
            kb.append([InlineKeyboardButton("Эксклюзив можно продать только через рынок", callback_data="noop")])
        kb.append([InlineKeyboardButton("Назад", callback_data=f"sell_back:{uid}")])
        await safe_del(query.message)
        await query.message.chat.send_message("Выберите редкость:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if action == "sell_rarity":
        card_id, rarity = int(parts[2]), parts[3]
        user_states[uid] = {'action': 'sell_qty', 'card_id': card_id, 'rarity': rarity}
        await safe_del(query.message)
        await query.message.chat.send_message("Введите количество:")
        return

    if action == "sell_do":
        card_id, rarity, qty, price = int(parts[2]), parts[3], int(parts[4]), int(parts[5])
        title = parts[6].replace(';', ':') if len(parts) > 6 else "Без названия"
        if not remove_user_card(uid, card_id, rarity, qty):
            await query.answer("Недостаточно карт.", show_alert=True)
            return
        with db_lock:
            conn = db()
            try:
                conn.execute("INSERT INTO market_listings (seller_id,card_id,rarity,quantity,price_diamonds,title) VALUES (?,?,?,?,?,?)", (uid, card_id, rarity, qty, price, title))
                conn.commit()
            finally:
                conn.close()
        sync_to_firebase()
        await safe_del(query.message)
        await query.message.chat.send_message("Карта выставлена на рынок.")
        return

    if action == "sell_cancel":
        user_states.pop(uid, None)
        await safe_del(query.message)
        await query.message.chat.send_message("Отменено.")
        return

    if action == "sell_back":
        cards = get_user_cards(uid)
        seen = set()
        kb = []
        for c in cards:
            if c['card_id'] not in seen:
                seen.add(c['card_id'])
                kb.append([InlineKeyboardButton(c['card_name'], callback_data=f"sell_pick:{uid}:{c['card_id']}")])
        kb.append([InlineKeyboardButton("Отмена", callback_data=f"sell_cancel:{uid}")])
        await safe_del(query.message)
        await query.message.chat.send_message("Выберите карту:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if action == "farm_toggle":
        u = get_user(uid)
        if not u['farm_card_id']:
            await query.answer("Сначала установите карточку в ферму.", show_alert=True)
            return
        new_status = 0 if u['farm_enabled'] else 1
        update_user(uid, 'farm_enabled', new_status)
        sync_to_firebase()
        status_text = "Активна" if new_status else "Неактивна"
        await query.answer(f"Ферма: {status_text}")
        await farm_cmd(update, ctx)
        return

    if action == "farm_change":
        cards = get_user_cards(uid)
        if not cards:
            await query.answer("Нет карт.", show_alert=True)
            return
        kb = []
        for c in cards:
            ri = RARITIES[c['rarity']]
            income = FARM_INCOME.get(c['rarity'], 0)
            kb.append([InlineKeyboardButton(f"{c['card_name']} {ri['e']} ({income}$/5мин)", callback_data=f"farm_set:{uid}:{c['card_id']}:{c['rarity']}")])
        kb.append([InlineKeyboardButton("Назад", callback_data=f"farm_back:{uid}")])
        await safe_del(query.message)
        await query.message.chat.send_message("Выберите карточку для фермы:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if action == "farm_set":
        card_id, rarity = int(parts[2]), parts[3]
        update_user(uid, 'farm_card_id', card_id)
        update_user(uid, 'farm_rarity', rarity)
        sync_to_firebase()
        with db_lock:
            conn = db()
            try:
                card = conn.execute("SELECT name FROM cards WHERE id=?", (card_id,)).fetchone()
            finally:
                conn.close()
        ri = RARITIES[rarity]
        income = FARM_INCOME.get(rarity, 0)
        await safe_del(query.message)
        await query.message.chat.send_message(f"Установлена карта «{card['name']}» {ri['e']}\nПрибыль: {income}$ каждые 5 минут")
        await farm_cmd(update, ctx)
        return

    if action == "farm_back":
        await farm_cmd(update, ctx)
        return

    if action == "sup_reply":
        ticket_id = int(parts[2])
        user_states[query.from_user.id] = {'action': 'admin_reply', 'ticket_id': ticket_id}
        await safe_del(query.message)
        await query.message.chat.send_message("Введите ответ для пользователя:")
        return

    if action == "sup_answer":
        ticket_id = int(parts[2])
        user_states[uid] = {'action': 'user_reply_to_admin', 'ticket_id': ticket_id}
        await safe_del(query.message)
        await query.message.chat.send_message("Введите ваш ответ техподдержке:")
        return

# === ТОЧКА ВХОДА ===
async def post_init(app):
    asyncio.create_task(farm_loop(app))

def main():
    init_db()
    restore_from_firebase() # Восстанавливаем данные из облака при старте
    
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
    
    logging.info("PeasCards запущен")
    app.run_polling()

if __name__ == '__main__':
    main()