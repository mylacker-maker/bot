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

SPIN_EMOJIS = ['🍎','🍊','🍋','🍇','🍉','🍓','','🍒','🥝','',
               '🌟','⭐','','🔥','❄️','','🎯','🎲','','👑',
               '🏆','💰','🎁','🔮','🧿','🍀','🌸','🌺','🦋','🐉',
               '🦄','🐺','🦊','🐱','🐶','🎃','','🤖','👾','']

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
user_states = {}
db_lock = threading.Lock()

# === FIREBASE ===
def firebase_get(path):
    try:
        url = f"{FIREBASE_URL}/{path}.json"
        with urllib.request.urlopen(url) as response:
            data = response.read().decode()
            return json.loads(data) if data else None
    except: return None

def firebase_put(path, data):
    try:
        url = f"{FIREBASE_URL}/{path}.json"
        req = urllib.request.Request(url, data=json.dumps(data).encode(), method='PUT')
        with urllib.request.urlopen(req): return True
    except: return False

def sync_users_to_firebase():
    with db_lock:
        conn = db()
        try:
            users = conn.execute("SELECT * FROM users").fetchall()
            firebase_put("users", {str(u['user_id']): dict(u) for u in users})
        finally: conn.close()

def sync_cards_to_firebase():
    with db_lock:
        conn = db()
        try:
            cards = conn.execute("SELECT * FROM cards").fetchall()
            firebase_put("cards", {str(c['id']): dict(c) for c in cards})
        finally: conn.close()

def restore_from_firebase():
    fb_users = firebase_get("users")
    fb_cards = firebase_get("cards")
    with db_lock:
        conn = db()
        try:
            if fb_users:
                for uid_str, u_data in fb_users.items():
                    uid = int(uid_str)
                    conn.execute("""INSERT INTO users (user_id, username, name, coins, diamonds, chance, 
                        last_peas, last_epic_peas, is_admin, is_banned, farm_enabled, farm_card_id, farm_rarity)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
                        username=excluded.username, coins=excluded.coins, diamonds=excluded.diamonds,
                        chance=excluded.chance, last_peas=excluded.last_peas, last_epic_peas=excluded.last_epic_peas,
                        is_admin=excluded.is_admin, is_banned=excluded.is_banned, farm_enabled=excluded.farm_enabled,
                        farm_card_id=excluded.farm_card_id, farm_rarity=excluded.farm_rarity""",
                        (uid, u_data.get('username'), u_data.get('name'), u_data.get('coins',0),
                         u_data.get('diamonds',0), u_data.get('chance',10.0), u_data.get('last_peas',0),
                         u_data.get('last_epic_peas',0), u_data.get('is_admin',0), u_data.get('is_banned',0),
                         u_data.get('farm_enabled',0), u_data.get('farm_card_id'), u_data.get('farm_rarity')))
            if fb_cards:
                for cid_str, c_data in fb_cards.items():
                    cid = int(cid_str)
                    conn.execute("""INSERT INTO cards (id, name, photo_file_id, author, excl_limit, excl_count)
                        VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name, photo_file_id=excluded.photo_file_id, author=excluded.author,
                        excl_limit=excluded.excl_limit, excl_count=excluded.excl_count""",
                        (cid, c_data.get('name'), c_data.get('photo_file_id'), c_data.get('author'),
                         c_data.get('excl_limit',0), c_data.get('excl_count',0)))
            conn.commit()
            logging.info("Данные восстановлены из Firebase")
        finally: conn.close()

# === БАЗА ДАННЫХ ===
def init_db():
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, name TEXT,
            coins INTEGER DEFAULT 0, diamonds INTEGER DEFAULT 0, chance REAL DEFAULT 10.0,
            last_peas REAL DEFAULT 0, last_epic_peas REAL DEFAULT 0, is_admin INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0, farm_enabled INTEGER DEFAULT 0, farm_card_id INTEGER, farm_rarity TEXT);
        CREATE TABLE IF NOT EXISTS cards (id INTEGER PRIMARY KEY, name TEXT, photo_file_id TEXT, author TEXT,
            excl_limit INTEGER DEFAULT 0, excl_count INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS user_cards (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            card_id INTEGER, rarity TEXT, quantity INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS market_listings (id INTEGER PRIMARY KEY AUTOINCREMENT, seller_id INTEGER,
            card_id INTEGER, rarity TEXT, quantity INTEGER, price_diamonds INTEGER, title TEXT);
        CREATE TABLE IF NOT EXISTS rest_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            card_name TEXT, photo_file_id TEXT, status TEXT DEFAULT 'pending');
        CREATE TABLE IF NOT EXISTS support_tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            user_message TEXT, admin_response TEXT, status TEXT DEFAULT 'open', created_at REAL DEFAULT 0);
    ''')
    conn.commit()
    conn.close()

def db():
    conn = sqlite3.connect(DB, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def get_user(uid):
    with db_lock:
        conn = db()
        try: return dict(conn.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone())
        finally: conn.close()

def get_user_by_username(username):
    with db_lock:
        conn = db()
        try: return dict(conn.execute("SELECT * FROM users WHERE LOWER(username)=LOWER(?)", (username.lstrip('@'),)).fetchone())
        finally: conn.close()

def ensure_user(uid, uname, name):
    with db_lock:
        conn = db()
        try:
            r = conn.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
            if not r:
                is_admin = 1 if uname and uname.lower() == ADMIN_USERNAME.lower() else 0
                conn.execute("INSERT INTO users (user_id,username,name,is_admin) VALUES (?,?,?,?)", (uid, uname, name, is_admin))
                conn.commit()
                sync_users_to_firebase()
                return dict(conn.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone())
            elif uname and uname.lower() == ADMIN_USERNAME.lower() and not r['is_admin']:
                conn.execute("UPDATE users SET is_admin=1 WHERE user_id=?", (uid,))
                conn.commit()
                sync_users_to_firebase()
            return dict(r)
        finally: conn.close()

def update_user(uid, field, value):
    with db_lock:
        conn = db()
        try:
            conn.execute(f"UPDATE users SET {field}=? WHERE user_id=?", (value, uid))
            conn.commit()
            sync_users_to_firebase()
        finally: conn.close()

def add_coins(uid, amount):
    with db_lock:
        conn = db()
        try:
            conn.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (amount, uid))
            conn.commit()
            sync_users_to_firebase()
        finally: conn.close()

def add_diamonds(uid, amount):
    with db_lock:
        conn = db()
        try:
            conn.execute("UPDATE users SET diamonds=diamonds+? WHERE user_id=?", (amount, uid))
            conn.commit()
            sync_users_to_firebase()
        finally: conn.close()

def get_all_cards():
    with db_lock:
        conn = db()
        try: return [dict(r) for r in conn.execute("SELECT * FROM cards").fetchall()]
        finally: conn.close()

def get_user_cards(uid):
    with db_lock:
        conn = db()
        try: return [dict(r) for r in conn.execute(
            "SELECT uc.*, c.name as card_name, c.photo_file_id FROM user_cards uc JOIN cards c ON uc.card_id=c.id WHERE uc.user_id=? AND uc.quantity>0", (uid,)).fetchall()]
        finally: conn.close()

def add_user_card(uid, card_id, rarity, qty=1):
    with db_lock:
        conn = db()
        try:
            existing = conn.execute("SELECT id FROM user_cards WHERE user_id=? AND card_id=? AND rarity=?", (uid, card_id, rarity)).fetchone()
            if existing: conn.execute("UPDATE user_cards SET quantity=quantity+? WHERE id=?", (qty, existing['id']))
            else: conn.execute("INSERT INTO user_cards (user_id,card_id,rarity,quantity) VALUES (?,?,?,?)", (uid, card_id, rarity, qty))
            conn.commit()
        finally: conn.close()

def remove_user_card(uid, card_id, rarity, qty=1):
    with db_lock:
        conn = db()
        try:
            existing = conn.execute("SELECT id,quantity FROM user_cards WHERE user_id=? AND card_id=? AND rarity=?", (uid, card_id, rarity)).fetchone()
            if not existing or existing["quantity"] < qty: return False
            conn.execute("UPDATE user_cards SET quantity=quantity-? WHERE id=?", (qty, existing["id"]))
            conn.commit()
            return True
        finally: conn.close()

def claim_card(uid, card_id, rarity):
    with db_lock:
        conn = db()
        try:
            card = conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
            if not card: return None
            if rarity == "Эксклюзивный":
                if card['excl_limit'] <= 0 or card['excl_count'] >= card['excl_limit']: return None
                conn.execute("UPDATE cards SET excl_count=excl_count+1 WHERE id=?", (card_id,))
                sync_cards_to_firebase()
            existing = conn.execute("SELECT id FROM user_cards WHERE user_id=? AND card_id=? AND rarity=?", (uid, card_id, rarity)).fetchone()
            if existing: conn.execute("UPDATE user_cards SET quantity=quantity+1 WHERE id=?", (existing["id"],))
            else: conn.execute("INSERT INTO user_cards (user_id,card_id,rarity,quantity) VALUES (?,?,?,1)", (uid, card_id, rarity))
            conn.commit()
            return dict(card)
        finally: conn.close()

def upgrade_card(uid, card_id, from_rarity):
    if from_rarity not in UPGRADE_PATH: return False, None, "Максимальная редкость"
    to_rarity = UPGRADE_PATH[from_rarity]
    cost = UPGRADE_COSTS[from_rarity]
    with db_lock:
        conn = db()
        try:
            user = conn.execute("SELECT coins FROM users WHERE user_id=?", (uid,)).fetchone()
            if not user or user['coins'] < cost: return False, None, "Недостаточно монет"
            if to_rarity == "Эксклюзивный":
                card = conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
                if not card or card['excl_limit'] <= 0 or card['excl_count'] >= card['excl_limit']: return False, None, "Нет эксклюзивов"
                if random.random() > 0.3:
                    conn.execute("UPDATE users SET coins=coins-? WHERE user_id=?", (cost, uid))
                    conn.commit()
                    sync_users_to_firebase()
                    return False, None, f"Неудача (30% шанс). Списано {cost}$"
                conn.execute("UPDATE cards SET excl_count=excl_count+1 WHERE id=?", (card_id,))
                sync_cards_to_firebase()
            conn.execute("UPDATE user_cards SET quantity=quantity-1 WHERE user_id=? AND card_id=? AND rarity=? AND quantity>0", (uid, card_id, from_rarity))
            existing = conn.execute("SELECT id FROM user_cards WHERE user_id=? AND card_id=? AND rarity=?", (uid, card_id, to_rarity)).fetchone()
            if existing: conn.execute("UPDATE user_cards SET quantity=quantity+1 WHERE id=?", (existing['id'],))
            else: conn.execute("INSERT INTO user_cards (user_id,card_id,rarity,quantity) VALUES (?,?,?,1)", (uid, card_id, to_rarity))
            conn.execute("UPDATE users SET coins=coins-? WHERE user_id=?", (cost, uid))
            conn.commit()
            sync_users_to_firebase()
            return True, to_rarity, None
        finally: conn.close()

def roll_rarity(chance, card):
    if random.uniform(0, 100) > chance: return 'Обычный'
    sub = random.uniform(0, 100)
    if sub < 50: return 'Редкий'
    if sub < 75: return 'Эпический'
    if sub < 90: return 'Мифический'
    if sub < 98: return 'Легендарный'
    if card['excl_limit'] > 0 and card['excl_count'] < card['excl_limit']: return 'Эксклюзивный'
    return 'Легендарный'

def pick_card():
    cards = get_all_cards()
    return random.choice(cards) if cards else None

def pick_three_cards():
    cards = get_all_cards()
    if not cards: return []
    return random.sample(cards, 3) if len(cards) >= 3 else [random.choice(cards) for _ in range(3)]

def gen_spin(): return ''.join(random.sample(SPIN_EMOJIS, 3))
def is_admin(uid):
    u = get_user(uid)
    return u and u['is_admin'] == 1
async def safe_del(msg):
    try: await msg.delete()
    except: pass
def parse_username(text):
    for word in text.split():
        if word.startswith('@'): return word.lstrip('@')
    return None

# === ФОНОВЫЕ ЗАДАЧИ ===
async def farm_loop(app):
    while True:
        try:
            await asyncio.sleep(300)
            with db_lock:
                conn = db()
                try:
                    users = conn.execute("SELECT user_id, farm_rarity FROM users WHERE farm_enabled=1 AND farm_card_id IS NOT NULL").fetchall()
                    for u in users:
                        income = FARM_INCOME.get(u['farm_rarity'], 0)
                        if income > 0: conn.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (income, u['user_id']))
                    conn.commit()
                    sync_users_to_firebase()
                finally: conn.close()
        except Exception as e:
            logging.error(f"Farm error: {e}")
            await asyncio.sleep(60)

# === КОМАНДЫ ===
async def start_cmd(update: Update, ctx):
    ensure_user(update.effective_user.id, update.effective_user.username, update.effective_user.first_name)
    await update.message.reply_text("Добро пожаловать в PeasCards.\n\nОсновные действия:\n• Горох — открыть попытку\n• Инвентарь — коллекция\n• Профиль — статистика\n\nКоманды:\n/shop — магазин\n/market — рынок\n/sell — продать карту\n/rest — предложить карту\n/farm — ферма\n/leaders — топ\n/teh — поддержка")

async def adm_cmd(update: Update, ctx):
    if not is_admin(update.effective_user.id): return await update.message.reply_text("Команда не найдена.")
    await update.message.reply_text("Админ-панель:\n/admin — управление\n/addcard — добавить карту\n/allcards — все карты\n/extlimit — продлить лимит\n/resel @user кол-во — выдать монеты\n/resel @all кол-во — всем монеты\n/money @user кол-во — выдать монеты\n/give — выдать карту\n/ungive — изъять карту\n/sp @user — карты пользователя")

async def admin_cmd(update: Update, ctx):
    if not is_admin(update.effective_user.id): return
    cards = get_all_cards()
    text = f"Админ-панель. Карт: {len(cards)}\n\n"
    for c in cards:
        excl = f"Эксклюзив: {c['excl_count']}/{c['excl_limit']}" if c['excl_limit'] > 0 else "Без эксклюзива"
        text += f"• {c['name']} ({excl})\n"
    await update.message.reply_text(text)

async def addcard_cmd(update: Update, ctx):
    if not is_admin(update.effective_user.id): return
    user_states[update.effective_user.id] = {'action': 'addcard_name'}
    await update.message.reply_text("Введите название карты:")

async def allcards_cmd(update: Update, ctx):
    if not is_admin(update.effective_user.id): return
    cards = get_all_cards()
    text = "Все карты:\n\n"
    for c in cards:
        excl = f"Эксклюзив: {c['excl_count']}/{c['excl_limit']}" if c['excl_limit'] > 0 else "Без эксклюзива"
        text += f"{c['name']} (ID: {c['id']})\n{excl}\n\n"
    await update.message.reply_text(text)

async def extlimit_cmd(update: Update, ctx):
    if not is_admin(update.effective_user.id): return
    cards = get_all_cards()
    kb = [[InlineKeyboardButton(c['name'], callback_data=f"extl_pick:{update.effective_user.id}:{c['id']}")] for c in cards if c['excl_limit'] > 0]
    if not kb: return await update.message.reply_text("Нет карт с эксклюзивами.")
    kb.append([InlineKeyboardButton("Отмена", callback_data="noop")])
    await update.message.reply_text("Выберите карту:", reply_markup=InlineKeyboardMarkup(kb))

async def resel_cmd(update: Update, ctx):
    if not is_admin(update.effective_user.id): return
    args = update.message.text.split()
    if len(args) < 3: return await update.message.reply_text("/resel @user кол-во или /resel @all кол-во")
    target = args[1].lower()
    try: amount = int(args[2])
    except: return await update.message.reply_text("Число должно быть числом.")
    if target == '@all':
        with db_lock:
            conn = db()
            try:
                conn.execute("UPDATE users SET coins = coins + ?", (amount,))
                conn.commit()
                sync_users_to_firebase()
            finally: conn.close()
        return await update.message.reply_text(f"Начислено {amount}$ всем.")
    t = get_user_by_username(target.lstrip('@'))
    if not t: return await update.message.reply_text("Пользователь не найден.")
    add_coins(t['user_id'], amount)
    await update.message.reply_text(f"Начислено {amount}$ @{target.lstrip('@')}")

async def give_cmd(update: Update, ctx):
    if not is_admin(update.effective_user.id): return
    cards = get_all_cards()
    kb = [[InlineKeyboardButton(c['name'], callback_data=f"give_pick:{update.effective_user.id}:{c['id']}")] for c in cards]
    kb.append([InlineKeyboardButton("Отмена", callback_data="noop")])
    user_states[update.effective_user.id] = {'action': 'give_card'}
    await update.message.reply_text("Выберите карту:", reply_markup=InlineKeyboardMarkup(kb))

async def sp_cmd(update: Update, ctx):
    if not is_admin(update.effective_user.id): return
    args = update.message.text.split()
    if len(args) < 2: return await update.message.reply_text("/sp @username")
    t = get_user_by_username(args[1].lstrip('@'))
    if not t: return await update.message.reply_text("Не найден.")
    cards = get_user_cards(t['user_id'])
    text = f"Карты @{args[1].lstrip('@')}:\n\n"
    for c in cards:
        ri = RARITIES[c['rarity']]
        text += f"{ri['e']} {c['card_name']} — {c['rarity']} x{c['quantity']}\n"
    await update.message.reply_text(text)

async def ungive_cmd(update: Update, ctx):
    if not is_admin(update.effective_user.id): return
    cards = get_all_cards()
    kb = [[InlineKeyboardButton(c['name'], callback_data=f"ungive_pick:{update.effective_user.id}:{c['id']}")] for c in cards]
    kb.append([InlineKeyboardButton("Отмена", callback_data="noop")])
    user_states[update.effective_user.id] = {'action': 'ungive_card'}
    await update.message.reply_text("Выберите карту:", reply_markup=InlineKeyboardMarkup(kb))

async def money_cmd(update: Update, ctx):
    if not is_admin(update.effective_user.id): return
    args = update.message.text.split()
    if update.message.reply_to_message:
        target_id = update.message.reply_to_message.from_user.id
        if len(args) < 2: return await update.message.reply_text("/money кол-во (ответом)")
        try: amount = int(args[1])
        except: return await update.message.reply_text("Число.")
    else:
        if len(args) < 3: return await update.message.reply_text("/money @user кол-во")
        t = get_user_by_username(args[1].lstrip('@'))
        if not t: return await update.message.reply_text("Не найден.")
        target_id = t['user_id']
        try: amount = int(args[2])
        except: return await update.message.reply_text("Число.")
    add_coins(target_id, amount)
    target = get_user(target_id)
    await update.message.reply_text(f"Начислено {amount}$ {target['name']}")

async def shop_cmd(update: Update, ctx):
    u = ensure_user(update.effective_user.id, update.effective_user.username, update.effective_user.first_name)
    if u['is_banned']: return await update.message.reply_text("Доступ ограничен.")
    now = time.time()
    cd = 86400 - (now - u['last_epic_peas'])
    wait = f"{int(cd//3600)}ч {int((cd%3600)//60)}мин" if cd > 0 else "Доступен"
    kb = [
        [InlineKeyboardButton("Улучшение шанса", callback_data=f"shop_chance:{u['user_id']}")],
        [InlineKeyboardButton("Обмен", callback_data=f"shop_exchange:{u['user_id']}")],
        [InlineKeyboardButton(f"Эпический горошек (1000$) - {wait}", callback_data=f"shop_epic:{u['user_id']}")],
    ]
    await update.message.reply_text(f"Магазин.\nБаланс: {u['coins']}$. Алмазы: {u['diamonds']}.\nШанс: {u['chance']}%", reply_markup=InlineKeyboardMarkup(kb))

async def rest_cmd(update: Update, ctx):
    u = ensure_user(update.effective_user.id, update.effective_user.username, update.effective_user.first_name)
    if u['is_banned']: return await update.message.reply_text("Доступ ограничен.")
    user_states[u['user_id']] = {'action': 'rest_name'}
    await update.message.reply_text("Введите название карты:")

async def market_cmd(update: Update, ctx):
    u = ensure_user(update.effective_user.id, update.effective_user.username, update.effective_user.first_name)
    if u['is_banned']: return await update.message.reply_text("Доступ ограничен.")
    with db_lock:
        conn = db()
        try:
            total = conn.execute("SELECT COUNT(*) FROM market_listings").fetchone()[0]
            my = conn.execute("SELECT COUNT(*) FROM market_listings WHERE seller_id=?", (u['user_id'],)).fetchone()[0]
        finally: conn.close()
    kb = [
        [InlineKeyboardButton("Рынок", callback_data=f"mkt_new:{u['user_id']}:0")],
        [InlineKeyboardButton("По категориям", callback_data=f"mkt_cards:{u['user_id']}")],
        [InlineKeyboardButton("Обмен", callback_data=f"mkt_exchange:{u['user_id']}")],
    ]
    await update.message.reply_text(f"Рынок.\nВсего: {total}\nВаших: {my}\n\n/sell — продать", reply_markup=InlineKeyboardMarkup(kb))

async def sell_cmd(update: Update, ctx):
    u = ensure_user(update.effective_user.id, update.effective_user.username, update.effective_user.first_name)
    if u['is_banned']: return await update.message.reply_text("Доступ ограничен.")
    cards = get_user_cards(u['user_id'])
    if not cards: return await update.message.reply_text("Нет карт.")
    seen = set()
    kb = [[InlineKeyboardButton(c['card_name'], callback_data=f"sell_pick:{u['user_id']}:{c['card_id']}")] for c in cards if c['card_id'] not in seen and not seen.add(c['card_id'])]
    kb.append([InlineKeyboardButton("Отмена", callback_data=f"sell_cancel:{u['user_id']}")])
    await update.message.reply_text("Выберите карту:", reply_markup=InlineKeyboardMarkup(kb))

async def farm_cmd(update: Update, ctx):
    u = ensure_user(update.effective_user.id, update.effective_user.username, update.effective_user.first_name)
    if u['is_banned']: return await update.message.reply_text("Доступ ограничен.")
    cards = get_user_cards(u['user_id'])
    if not cards: return await update.message.reply_text("Нужна карта. Получите через Горох.")
    status = "Активна" if u['farm_enabled'] else "Неактивна"
    info = "Не установлена"
    if u['farm_card_id'] and u['farm_rarity']:
        with db_lock:
            conn = db()
            try:
                card = conn.execute("SELECT * FROM cards WHERE id=?", (u['farm_card_id'],)).fetchone()
                if card:
                    ri = RARITIES[u['farm_rarity']]
                    info = f"{card['name']} {ri['e']} ({FARM_INCOME.get(u['farm_rarity'],0)}$/5мин)"
            finally: conn.close()
    kb = [
        [InlineKeyboardButton("Сменить карту", callback_data=f"farm_change:{u['user_id']}")],
        [InlineKeyboardButton(f"{'Отключить' if u['farm_enabled'] else 'Включить'}", callback_data=f"farm_toggle:{u['user_id']}")],
    ]
    await update.message.reply_text(f"Ферма.\nСтатус: {status}\nКарта: {info}", reply_markup=InlineKeyboardMarkup(kb))

async def leaders_cmd(update: Update, ctx):
    with db_lock:
        conn = db()
        try: leaders = conn.execute("SELECT user_id, name, username, coins FROM users WHERE is_banned=0 ORDER BY coins DESC LIMIT 10").fetchall()
        finally: conn.close()
    if not leaders: return await update.message.reply_text("Нет игроков.")
    text = "Топ-10:\n\n"
    for i, l in enumerate(leaders, 1):
        medal = "🥇" if i==1 else "🥈" if i==2 else "🥉" if i==3 else f"{i}."
        uname = f"@{l['username']}" if l['username'] else l['name']
        text += f"{medal} {uname} — {l['coins']}$\n"
    await update.message.reply_text(text)

async def teh_cmd(update: Update, ctx):
    u = ensure_user(update.effective_user.id, update.effective_user.username, update.effective_user.first_name)
    if u['is_banned']: return await update.message.reply_text("Доступ ограничен.")
    user_states[u['user_id']] = {'action': 'support_msg'}
    await update.message.reply_text("Напишите сообщение для поддержки:")

# === ОБРАБОТКА ТЕКСТА ===
async def handle_text(update: Update, ctx):
    if not update.message or not update.message.text: return
    uid = update.effective_user.id
    text = update.message.text.strip()
    u = ensure_user(uid, update.effective_user.username, update.effective_user.first_name)
    
    if text.lower() == 'магазин': return await shop_cmd(update, ctx)
    if text.lower() == 'рынок': return await market_cmd(update, ctx)
    if text.lower() == 'ферма': return await farm_cmd(update, ctx)
    if text.lower() in ['техподдержка', 'тех поддержка']: return await teh_cmd(update, ctx)
    if text.lower() == 'лидеры': return await leaders_cmd(update, ctx)
    
    if text.lower() == 'профиль' and update.message.reply_to_message:
        target = update.message.reply_to_message.from_user
        tu = get_user(target.id)
        if not tu: return await update.message.reply_text("Не зарегистрирован.")
        tc = get_user_cards(target.id)
        await update.message.reply_text(f"Профиль {tu['name']}\nМонеты: {tu['coins']}$. Алмазы: {tu['diamonds']}\nШанс: {tu['chance']}%\nКарт: {sum(c['quantity'] for c in tc)}")
        return
    
    if text.lower() == 'профиль':
        tc = get_user_cards(uid)
        await update.message.reply_text(f"Ваш профиль\nМонеты: {u['coins']}$. Алмазы: {u['diamonds']}\nШанс: {u['chance']}%\nКарт: {sum(c['quantity'] for c in tc)}")
        return
    
    if text.lower() == 'инвентарь':
        cards = get_user_cards(uid)
        if not cards: return await update.message.reply_text("Пуст.")
        order = {'Эксклюзивный':0, 'Легендарный':1, 'Мифический':2, 'Эпический':3, 'Редкий':4, 'Обычный':5}
        cards = sorted(cards, key=lambda c: order.get(c['rarity'], 99))
        kb = [[InlineKeyboardButton(f"{c['card_name']} {RARITIES[c['rarity']]['e']}, {c['quantity']}", callback_data=f"inv_item:{uid}:{c['card_id']}:{c['rarity']}")] for c in cards]
        kb.append([InlineKeyboardButton("Закрыть", callback_data="noop")])
        await update.message.reply_text("Инвентарь", reply_markup=InlineKeyboardMarkup(kb))
        return
    
    if 'горох' in text.lower():
        if u['is_banned']: return
        now = time.time()
        if now - u['last_peas'] < 300: return await update.message.reply_text(f"Подождите {int(300-(now-u['last_peas']))} сек.")
        all_cards = get_all_cards()
        if not all_cards: return await update.message.reply_text("Пул пуст.")
        update_user(uid, 'last_peas', now)
        msg = await update.message.reply_text(f"Открываем… {gen_spin()}")
        for _ in range(3):
            await asyncio.sleep(0.35)
            try: await msg.edit_text(f"Определяем… {gen_spin()}")
            except: pass
        kb = [[InlineKeyboardButton("Результат", callback_data=f"spin_free:{uid}")]]
        try: await msg.edit_text(f"Готово.\nШанс: {u['chance']}%", reply_markup=InlineKeyboardMarkup(kb))
        except: pass
        return
    
    state = user_states.get(uid)
    if not state: return
    action = state.get('action')
    
    if action == 'support_msg':
        with db_lock:
            conn = db()
            try:
                conn.execute("INSERT INTO support_tickets (user_id,user_message,created_at) VALUES (?,?,?)", (uid, text, time.time()))
                tid = conn.execute("SELECT last_insert_rowid()")[0]
                conn.commit()
            finally: conn.close()
        user_states.pop(uid, None)
        await update.message.reply_text("Отправлено.")
        admin = get_user_by_username(ADMIN_USERNAME)
        if admin:
            kb = [[InlineKeyboardButton("Ответить", callback_data=f"sup_reply:{admin['user_id']}:{tid}")]]
            try: await ctx.bot.send_message(admin['user_id'], f"Заявка #{tid} от {u['name']}:\n\n{text}", reply_markup=InlineKeyboardMarkup(kb))
            except: pass
        return
    
    if action == 'admin_reply':
        tid = state['ticket_id']
        with db_lock:
            conn = db()
            try:
                conn.execute("UPDATE support_tickets SET admin_response=?, status='answered' WHERE id=?", (text, tid))
                conn.commit()
            finally: conn.close()
        user_states.pop(uid, None)
        await update.message.reply_text("Отправлено.")
        return
    
    if action == 'rest_name':
        user_states[uid] = {'action': 'rest_photo', 'card_name': text}
        await update.message.reply_text("Пришлите фото карты:")
        return
    
    if action == 'addcard_name':
        user_states[uid] = {'action': 'addcard_photo', 'card_name': text}
        await update.message.reply_text("Пришлите фото:")
        return
    
    if action == 'addcard_excl':
        try:
            limit = int(text)
            if limit < 0: limit = 0
            with db_lock:
                conn = db()
                try:
                    conn.execute("INSERT INTO cards (name,photo_file_id,author,excl_limit) VALUES (?,?,?,?)", (state['card_name'], state['photo_file_id'], 'PeasCards', limit))
                    conn.commit()
                    sync_cards_to_firebase()
                finally: conn.close()
            user_states.pop(uid, None)
            await update.message.reply_text(f"Карта добавлена. Эксклюзивов: {limit}")
        except: await update.message.reply_text("Число.")
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
                    if req:
                        conn.execute("INSERT INTO cards (name,photo_file_id,author,excl_limit) VALUES (?,?,?,?)", (req['card_name'], req['photo_file_id'], 'PeasCards', limit))
                        conn.execute("UPDATE rest_requests SET status='accepted' WHERE id=?", (req_id,))
                        conn.commit()
                        sync_cards_to_firebase()
                finally: conn.close()
            user_states.pop(uid, None)
            await update.message.reply_text(f"Принято. Эксклюзивов: {limit}")
        except: await update.message.reply_text("Число.")
        return
    
    if action == 'extlimit_amount':
        try:
            amount = int(text)
            if amount < 0: return await update.message.reply_text("Положительное.")
            card_id = state['card_id']
            with db_lock:
                conn = db()
                try:
                    conn.execute("UPDATE cards SET excl_limit=excl_limit+? WHERE id=?", (amount, card_id))
                    conn.commit()
                    sync_cards_to_firebase()
                    card = conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
                finally: conn.close()
            user_states.pop(uid, None)
            await update.message.reply_text(f"Лимит: {card['excl_limit']}. Осталось: {card['excl_limit']-card['excl_count']}")
        except: await update.message.reply_text("Число.")
        return
    
    if action == 'give_username':
        username = parse_username(text)
        if not username: return await update.message.reply_text("@username")
        target = get_user_by_username(username)
        if not target:
            user_states.pop(uid, None)
            return await update.message.reply_text("Не найден.")
        user_states[uid]['target_id'] = target['user_id']
        user_states[uid]['action'] = 'give_amount'
        await update.message.reply_text(f"Количество для @{username}:")
        return
    
    if action == 'give_amount':
        try:
            qty = int(text)
            if qty < 1: return await update.message.reply_text("Минимум 1.")
            add_user_card(user_states[uid]['target_id'], state['card_id'], state['rarity'], qty)
            user_states.pop(uid, None)
            await update.message.reply_text(f"Выдано {qty} карт.")
        except: await update.message.reply_text("Число.")
        return
    
    if action == 'ungive_username':
        username = parse_username(text)
        if not username: return await update.message.reply_text("@username")
        target = get_user_by_username(username)
        if not target:
            user_states.pop(uid, None)
            return await update.message.reply_text("Не найден.")
        user_states[uid]['target_id'] = target['user_id']
        user_states[uid]['action'] = 'ungive_amount'
        await update.message.reply_text(f"Количество у @{username}:")
        return
    
    if action == 'ungive_amount':
        try:
            qty = int(text)
            if qty < 1: return await update.message.reply_text("Минимум 1.")
            if not remove_user_card(user_states[uid]['target_id'], state['card_id'], state['rarity'], qty):
                user_states.pop(uid, None)
                return await update.message.reply_text("Недостаточно.")
            user_states.pop(uid, None)
            await update.message.reply_text(f"Изъято {qty} карт.")
        except: await update.message.reply_text("Число.")
        return
    
    if action == 'mkt_exchange_input':
        if text.lower() in ['отмена', 'назад']:
            user_states.pop(uid, None)
            return await update.message.reply_text("Отменено.")
        try:
            amount = int(text)
            if amount < 100: return await update.message.reply_text("Минимум 100.")
            if amount > u['coins']: return await update.message.reply_text("Недостаточно.")
            diamonds = int(amount / 10)
            kb = [[InlineKeyboardButton("Обменять", callback_data=f"exch_do:{uid}:{amount}:{diamonds}")], [InlineKeyboardButton("Отмена", callback_data=f"exch_cancel:{uid}")]]
            user_states.pop(uid, None)
            await update.message.reply_text(f"{amount}$ → {diamonds}💎?", reply_markup=InlineKeyboardMarkup(kb))
        except: await update.message.reply_text("Число.")
        return
    
    if action == 'sell_qty':
        try:
            qty = int(text)
            if qty < 1: return await update.message.reply_text("Минимум 1.")
            with db_lock:
                conn = db()
                try: uc = conn.execute("SELECT quantity FROM user_cards WHERE user_id=? AND card_id=? AND rarity=?", (uid, state['card_id'], state['rarity'])).fetchone()
                finally: conn.close()
            if not uc or uc['quantity'] < qty: return await update.message.reply_text("Недостаточно.")
            user_states[uid] = {'action': 'sell_title', 'card_id': state['card_id'], 'rarity': state['rarity'], 'qty': qty}
            await update.message.reply_text("Название объявления:")
        except: await update.message.reply_text("Число.")
        return
    
    if action == 'sell_title':
        user_states[uid]['title'] = text
        user_states[uid]['action'] = 'sell_price'
        await update.message.reply_text("Цена в алмазах (мин 10):")
        return
    
    if action == 'sell_price':
        try:
            price = int(text)
            if price < 10: return await update.message.reply_text("Минимум 10.")
            s = user_states[uid]
            earnings = int(price * 10 * 0.9 * s['qty'])
            kb = [[InlineKeyboardButton("Да", callback_data=f"sell_do:{uid}:{s['card_id']}:{s['rarity']}:{s['qty']}:{price}:{s['title'].replace(':',';')}")], [InlineKeyboardButton("Нет", callback_data=f"sell_cancel:{uid}")]]
            user_states.pop(uid, None)
            await update.message.reply_text(f"Выручка: {earnings}$. Подтвердить?", reply_markup=InlineKeyboardMarkup(kb))
        except: await update.message.reply_text("Число.")
        return

# === ОБРАБОТКА ФОТО ===
async def handle_photo(update: Update, ctx):
    uid = update.effective_user.id
    state = user_states.get(uid)
    if not state: return
    photo = update.message.photo[-1]
    file = await ctx.bot.get_file(photo.file_id)
    
    if state.get('action') == 'rest_photo':
        user_states[uid] = {'action': 'rest_confirm', 'card_name': state['card_name'], 'photo_file_id': photo.file_id}
        kb = [[InlineKeyboardButton("Отправить", callback_data=f"rest_send:{uid}")], [InlineKeyboardButton("Отмена", callback_data=f"rest_cancel:{uid}")]]
        await update.message.reply_photo(photo=photo.file_id, caption=f"Карта: {state['card_name']}\nПодтвердить?", reply_markup=InlineKeyboardMarkup(kb))
        return
    
    if state.get('action') == 'addcard_photo':
        user_states[uid] = {'action': 'addcard_excl', 'card_name': state['card_name'], 'photo_file_id': photo.file_id}
        await update.message.reply_text("Сколько эксклюзивов? (0 = без):")
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
        if query.from_user.id != uid: return await query.answer("Не ваша кнопка.", show_alert=True)
    
    if action == "spin_free":
        u = get_user(uid)
        card = pick_card()
        if not card: return await query.message.chat.send_message("Пул пуст.")
        rarity = roll_rarity(u['chance'], card)
        if rarity in ['Обычный', 'Редкий']: update_user(uid, 'chance', min(u['chance'] + 2.5, 100))
        else: update_user(uid, 'chance', 10.0)
        ri = RARITIES[rarity]
        remaining = f"{card['excl_limit'] - card['excl_count']}" if card['excl_limit'] > 0 else "Неограниченно"
        kb = [[InlineKeyboardButton("Получить", callback_data=f"get_card:{uid}:{card['id']}:{rarity}")]]
        if rarity != 'Эксклюзивный': kb[0].append(InlineKeyboardButton(f"Продать {ri['price']}$", callback_data=f"sell_drop:{uid}:{card['id']}:{rarity}"))
        text = f"{card['name'].upper()}\n{rarity.upper()} {ri['e']}\n\nОсталось: {remaining}"
        await safe_del(query.message)
        if card.get('photo_file_id'): await query.message.chat.send_photo(photo=card['photo_file_id'], caption=text, reply_markup=InlineKeyboardMarkup(kb))
        else: await query.message.chat.send_message(text, reply_markup=InlineKeyboardMarkup(kb))
        return
    
    if action == "shop_epic":
        u = get_user(uid)
        now = time.time()
        cd = 86400 - (now - u['last_epic_peas'])
        if cd > 0: return await query.answer(f"Через {int(cd//3600)}ч {int((cd%3600)//60)}мин", show_alert=True)
        if u['coins'] < 1000: return await query.answer("Нужно 1000$.", show_alert=True)
        update_user(uid, 'last_epic_peas', now)
        update_user(uid, 'coins', u['coins'] - 1000)
        msg = await query.message.chat.send_message(f"Эпический горошек… {gen_spin()}")
        for _ in range(5):
            await asyncio.sleep(0.5)
            try: await msg.edit_text(f"Результат… {gen_spin()}")
            except: pass
        kb = [[InlineKeyboardButton("Получить", callback_data=f"epic_spin:{uid}")]]
        try: await msg.edit_text("Готово. 3 карты.", reply_markup=InlineKeyboardMarkup(kb))
        except: pass
        return
    
    if action == "epic_spin":
        u = get_user(uid)
        cards = pick_three_cards()
        if not cards: return await query.message.chat.send_message("Пул пуст.")
        results = []
        media = []
        for card in cards:
            rarity = roll_rarity(u['chance'], card)
            ri = RARITIES[rarity]
            if claim_card(uid, card['id'], rarity): results.append(f"{card['name']} — {rarity} {ri['e']}")
            if card.get('photo_file_id'): media.append(InputMediaPhoto(media=card['photo_file_id'], caption=f"{card['name']}\n{rarity} {ri['e']}"))
        await safe_del(query.message)
        if media: await query.message.chat.send_media_group(media=media)
        if results: await query.message.chat.send_message("Эпический горошек:\n\n" + "\n".join(results))
        else: await query.message.chat.send_message("Получено.")
        return
    
    if action == "get_card":
        card_id, rarity = int(parts[2]), parts[3]
        if not claim_card(uid, card_id, rarity): return await query.answer("Недоступна.", show_alert=True)
        await safe_del(query.message)
        await query.message.chat.send_message(f"Получено!")
        if len(get_user_cards(uid)) == 1: await query.message.chat.send_message("Теперь можно включить ферму. /farm")
        return
    
    if action == "sell_drop":
        card_id, rarity = int(parts[2]), parts[3]
        add_coins(uid, RARITIES[rarity]['price'])
        await safe_del(query.message)
        await query.message.chat.send_message(f"Продано за {RARITIES[rarity]['price']}$")
        return
    
    if action == "inv_item":
        card_id, rarity = int(parts[2]), parts[3]
        with db_lock:
            conn = db()
            try:
                card = conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
                uc = conn.execute("SELECT quantity FROM user_cards WHERE user_id=? AND card_id=? AND rarity=?", (uid, card_id, rarity)).fetchone()
            finally: conn.close()
        if not card or not uc: return
        ri = RARITIES[rarity]
        price = ri['price']
        kb = [[InlineKeyboardButton(f"Продать 1 за {price}$", callback_data=f"inv_sell1:{uid}:{card_id}:{rarity}")]]
        if uc['quantity'] > 1: kb.append([InlineKeyboardButton(f"Продать все ({uc['quantity']}) за {price*uc['quantity']}$", callback_data=f"inv_sellall:{uid}:{card_id}:{rarity}")])
        if rarity != 'Эксклюзивный' and rarity in UPGRADE_PATH:
            kb.append([InlineKeyboardButton(f"Улучшить до {UPGRADE_PATH[rarity]} ({UPGRADE_COSTS[rarity]}$)", callback_data=f"upgrade:{uid}:{card_id}:{rarity}")])
        kb.append([InlineKeyboardButton("Назад", callback_data=f"inv_back:{uid}")])
        txt = f"{card['name']} {ri['e']} {rarity}\nКоличество: {uc['quantity']}"
        await safe_del(query.message)
        if card.get('photo_file_id'): await query.message.chat.send_photo(photo=card['photo_file_id'], caption=txt, reply_markup=InlineKeyboardMarkup(kb))
        else: await query.message.chat.send_message(txt, reply_markup=InlineKeyboardMarkup(kb))
        return
    
    if action == "inv_sell1":
        card_id, rarity = int(parts[2]), parts[3]
        if not remove_user_card(uid, card_id, rarity, 1): return await query.answer("Продана.", show_alert=True)
        add_coins(uid, RARITIES[rarity]['price'])
        await safe_del(query.message)
        await query.message.chat.send_message(f"Продано за {RARITIES[rarity]['price']}$")
        return
    
    if action == "inv_sellall":
        card_id, rarity = int(parts[2]), parts[3]
        with db_lock:
            conn = db()
            try: uc = conn.execute("SELECT quantity FROM user_cards WHERE user_id=? AND card_id=? AND rarity=?", (uid, card_id, rarity)).fetchone()
            finally: conn.close()
        if not uc: return await query.answer("Проданы.", show_alert=True)
        qty = uc['quantity']
        for _ in range(qty): remove_user_card(uid, card_id, rarity, 1)
        add_coins(uid, RARITIES[rarity]['price'] * qty)
        await safe_del(query.message)
        await query.message.chat.send_message(f"Продано {qty} за {RARITIES[rarity]['price']*qty}$")
        return
    
    if action == "upgrade":
        card_id, from_rarity = int(parts[2]), parts[3]
        success, new_rarity, err = upgrade_card(uid, card_id, from_rarity)
        if success:
            await safe_del(query.message)
            await query.message.chat.send_message(f"Улучшено до {new_rarity}!")
        else: await query.answer(err or "Ошибка", show_alert=True)
        return
    
    if action == "inv_back":
        cards = get_user_cards(uid)
        if not cards: return await query.message.chat.send_message("Пуст.")
        order = {'Эксклюзивный':0, 'Легендарный':1, 'Мифический':2, 'Эпический':3, 'Редкий':4, 'Обычный':5}
        cards = sorted(cards, key=lambda c: order.get(c['rarity'], 99))
        kb = [[InlineKeyboardButton(f"{c['card_name']} {RARITIES[c['rarity']]['e']}, {c['quantity']}", callback_data=f"inv_item:{uid}:{c['card_id']}:{c['rarity']}")] for c in cards]
        kb.append([InlineKeyboardButton("Закрыть", callback_data="noop")])
        await safe_del(query.message)
        await query.message.chat.send_message("Инвентарь", reply_markup=InlineKeyboardMarkup(kb))
        return
    
    if action == "rest_send":
        state = user_states.get(uid)
        if not state: return await query.answer("Ошибка", show_alert=True)
        with db_lock:
            conn = db()
            try:
                conn.execute("INSERT INTO rest_requests (user_id,card_name,photo_file_id) VALUES (?,?,?)", (uid, state['card_name'], state['photo_file_id']))
                req_id = conn.execute("SELECT last_insert_rowid()")[0]
                conn.commit()
            finally: conn.close()
        user_states.pop(uid, None)
        await safe_del(query.message)
        await query.message.chat.send_message("Отправлено на модерацию.")
        admin = get_user_by_username(ADMIN_USERNAME)
        if admin:
            u = get_user(uid)
            uname = f"@{u['username']}" if u['username'] else u['name']
            caption = f"Заявка от {u['name']} ({uname})\nКарта: {state['card_name']}"
            kb = [[InlineKeyboardButton("Принять", callback_data=f"admin_acc:{admin['user_id']}:{req_id}")], [InlineKeyboardButton("Отклонить", callback_data=f"admin_rej:{admin['user_id']}:{req_id}")], [InlineKeyboardButton("Бан", callback_data=f"admin_ban:{admin['user_id']}:{req_id}")]]
            try:
                if state.get('photo_file_id'): await ctx.bot.send_photo(admin['user_id'], photo=state['photo_file_id'], caption=caption, reply_markup=InlineKeyboardMarkup(kb))
                else: await ctx.bot.send_message(admin['user_id'], caption, reply_markup=InlineKeyboardMarkup(kb))
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
        await query.message.chat.send_message("Сколько эксклюзивов? (0 = без):")
        return
    
    if action == "admin_rej":
        if not is_admin(query.from_user.id): return
        req_id = int(parts[2])
        with db_lock:
            conn = db()
            try:
                req = conn.execute("SELECT * FROM rest_requests WHERE id=?", (req_id,)).fetchone()
                if req: conn.execute("UPDATE rest_requests SET status='rejected' WHERE id=?", (req_id,))
                conn.commit()
            finally: conn.close()
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
                    sync_users_to_firebase()
            finally: conn.close()
        if req:
            try: await ctx.bot.send_message(req['user_id'], "Доступ ограничен.")
            except: pass
        await safe_del(query.message)
        await query.message.chat.send_message("Заблокирован.")
        return
    
    if action == "extl_pick":
        card_id = int(parts[2])
        user_states[uid] = {'action': 'extlimit_amount', 'card_id': card_id}
        with db_lock:
            conn = db()
            try: card = conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
            finally: conn.close()
        await safe_del(query.message)
        await query.message.chat.send_message(f"Карта: {card['name']}\nЛимит: {card['excl_limit']}\nОсталось: {card['excl_limit']-card['excl_count']}\n\nСколько добавить?")
        return
    
    if action == "give_pick":
        card_id = int(parts[2])
        user_states[uid] = {'action': 'give_rarity', 'card_id': card_id}
        kb = [[InlineKeyboardButton(f"{ri['e']} {rn}", callback_data=f"give_rar:{uid}:{card_id}:{rn}")] for rn, ri in RARITIES.items()]
        kb.append([InlineKeyboardButton("Отмена", callback_data="noop")])
        await safe_del(query.message)
        await query.message.chat.send_message("Редкость:", reply_markup=InlineKeyboardMarkup(kb))
        return
    
    if action == "give_rar":
        card_id, rarity = int(parts[2]), parts[3]
        user_states[uid] = {'action': 'give_username', 'card_id': card_id, 'rarity': rarity}
        await safe_del(query.message)
        await query.message.chat.send_message("@username:")
        return
    
    if action == "ungive_pick":
        card_id = int(parts[2])
        user_states[uid] = {'action': 'ungive_rarity', 'card_id': card_id}
        kb = [[InlineKeyboardButton(f"{ri['e']} {rn}", callback_data=f"ungive_rar:{uid}:{card_id}:{rn}")] for rn, ri in RARITIES.items()]
        kb.append([InlineKeyboardButton("Отмена", callback_data="noop")])
        await safe_del(query.message)
        await query.message.chat.send_message("Редкость:", reply_markup=InlineKeyboardMarkup(kb))
        return
    
    if action == "ungive_rar":
        card_id, rarity = int(parts[2]), parts[3]
        user_states[uid] = {'action': 'ungive_username', 'card_id': card_id, 'rarity': rarity}
        await safe_del(query.message)
        await query.message.chat.send_message("@username:")
        return
    
    if action == "shop_chance":
        kb = [[InlineKeyboardButton("1% — 100$", callback_data=f"shop_buy:{uid}:100:1")], [InlineKeyboardButton("2.5% — 200$", callback_data=f"shop_buy:{uid}:200:2.5")], [InlineKeyboardButton("15% — 500$", callback_data=f"shop_buy:{uid}:500:15")], [InlineKeyboardButton("30% — 1000$", callback_data=f"shop_buy:{uid}:1000:30")], [InlineKeyboardButton("100% — 5000$", callback_data=f"shop_buy:{uid}:5000:100")], [InlineKeyboardButton("Назад", callback_data=f"shop_back:{uid}")]]
        u = get_user(uid)
        await safe_del(query.message)
        await query.message.chat.send_message(f"Улучшение шанса.\nБаланс: {u['coins']}$. Шанс: {u['chance']}%", reply_markup=InlineKeyboardMarkup(kb))
        return
    
    if action == "shop_exchange":
        user_states[uid] = {'action': 'mkt_exchange_input'}
        await safe_del(query.message)
        await query.message.chat.send_message("Сколько монет? (100$ = 10💎, мин 100):")
        return
    
    if action == "shop_back":
        u = get_user(uid)
        now = time.time()
        cd = 86400 - (now - u['last_epic_peas'])
        wait = f"{int(cd//3600)}ч {int((cd%3600)//60)}мин" if cd > 0 else "Доступен"
        kb = [[InlineKeyboardButton("Улучшение шанса", callback_data=f"shop_chance:{uid}")], [InlineKeyboardButton("Обмен", callback_data=f"shop_exchange:{uid}")], [InlineKeyboardButton(f"Эпический горошек (1000$) - {wait}", callback_data=f"shop_epic:{uid}")]]
        await safe_del(query.message)
        await query.message.chat.send_message(f"Магазин.\nБаланс: {u['coins']}$. Алмазы: {u['diamonds']}", reply_markup=InlineKeyboardMarkup(kb))
        return
    
    if action == "shop_buy":
        cost, bonus = int(parts[2]), float(parts[3])
        u = get_user(uid)
        if u['coins'] < cost: return await query.answer("Недостаточно.", show_alert=True)
        with db_lock:
            conn = db()
            try:
                conn.execute("UPDATE users SET coins=coins-?, chance=MIN(chance+?,100) WHERE user_id=?", (cost, bonus, uid))
                conn.commit()
                sync_users_to_firebase()
            finally: conn.close()
        u = get_user(uid)
        await safe_del(query.message)
        await query.message.chat.send_message(f"Шанс: {u['chance']}%. Баланс: {u['coins']}$")
        return
    
    if action == "mkt_exchange":
        user_states[uid] = {'action': 'mkt_exchange_input'}
        await safe_del(query.message)
        await query.message.chat.send_message("Сколько монет? (100$ = 10💎, мин 100):")
        return
    
    if action == "exch_do":
        amount, diamonds = int(parts[2]), int(parts[3])
        u = get_user(uid)
        if u['coins'] < amount: return await query.answer("Недостаточно.", show_alert=True)
        with db_lock:
            conn = db()
            try:
                conn.execute("UPDATE users SET coins=coins-?, diamonds=diamonds+? WHERE user_id=?", (amount, diamonds, uid))
                conn.commit()
                sync_users_to_firebase()
            finally: conn.close()
        await safe_del(query.message)
        await query.message.chat.send_message(f"Обменено. +{diamonds}💎")
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
                listings = conn.execute("SELECT ml.*, u.name as sn, u.username as su, c.name as cn, c.photo_file_id FROM market_listings ml JOIN users u ON ml.seller_id=u.user_id JOIN cards c ON ml.card_id=c.id ORDER BY ml.price_diamonds ASC LIMIT ? OFFSET ?", (per_page, offset)).fetchall()
            finally: conn.close()
        if not listings and page == 0: return await query.message.chat.send_message("Пусто.")
        kb = []
        for l in listings:
            ri = RARITIES[l['rarity']]
            uname = f"@{l['su']}" if l['su'] else l['sn']
            kb.append([InlineKeyboardButton(f"{l['cn']} {ri['e']} • {l['quantity']} алм. {l['price_diamonds']} | {uname}", callback_data=f"mkt_item:{uid}:{l['id']}")])
        nav = []
        if page > 0: nav.append(InlineKeyboardButton("Назад", callback_data=f"mkt_new:{uid}:{page-1}"))
        if offset + per_page < total: nav.append(InlineKeyboardButton("Далее", callback_data=f"mkt_new:{uid}:{page+1}"))
        if nav: kb.append(nav)
        kb.append([InlineKeyboardButton("Назад", callback_data=f"mkt_back:{uid}")])
        await safe_del(query.message)
        await query.message.chat.send_message(f"Рынок. Стр. {page+1}", reply_markup=InlineKeyboardMarkup(kb))
        return
    
    if action == "mkt_item":
        lid = int(parts[2])
        with db_lock:
            conn = db()
            try: l = conn.execute("SELECT ml.*, u.name as sn, u.username as su, c.name as cn, c.photo_file_id FROM market_listings ml JOIN users u ON ml.seller_id=u.user_id JOIN cards c ON ml.card_id=c.id WHERE ml.id=?", (lid,)).fetchone()
            finally: conn.close()
        if not l: return await query.answer("Недоступно.", show_alert=True)
        ri = RARITIES[l['rarity']]
        uname = f"@{l['su']}" if l['su'] else l['sn']
        txt = f"Товар #{l['id']}\nНазвание: {l['title']}\nКарта: {l['cn']} {ri['e']}\nПродавец: {l['sn']} ({uname})\nКол-во: {l['quantity']}\nЦена: {l['price_diamonds']} алмазов"
        kb = [[InlineKeyboardButton(f"Купить {l['price_diamonds']} алм.", callback_data=f"mkt_buy:{uid}:{lid}")], [InlineKeyboardButton("Назад", callback_data=f"mkt_new:{uid}:0")]]
        await safe_del(query.message)
        if l.get('photo_file_id'): await query.message.chat.send_photo(photo=l['photo_file_id'], caption=txt, reply_markup=InlineKeyboardMarkup(kb))
        else: await query.message.chat.send_message(txt, reply_markup=InlineKeyboardMarkup(kb))
        return
    
    if action == "mkt_buy":
        lid = int(parts[2])
        with db_lock:
            conn = db()
            try: l = conn.execute("SELECT * FROM market_listings WHERE id=?", (lid,)).fetchone()
            finally: conn.close()
        if not l: return await query.answer("Недоступно.", show_alert=True)
        u = get_user(uid)
        if u['diamonds'] < l['price_diamonds']: return await query.answer("Недостаточно алмазов.", show_alert=True)
        if l['seller_id'] == uid: return await query.answer("Нельзя у себя.", show_alert=True)
        earnings = int(l['price_diamonds'] * 10 * 0.9)
        with db_lock:
            conn = db()
            try:
                conn.execute("UPDATE users SET diamonds=diamonds-? WHERE user_id=?", (l['price_diamonds'], uid))
                conn.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (earnings, l['seller_id']))
                conn.execute("DELETE FROM market_listings WHERE id=?", (lid,))
                conn.commit()
                sync_users_to_firebase()
            finally: conn.close()
        add_user_card(uid, l['card_id'], l['rarity'], l['quantity'])
        await safe_del(query.message)
        await query.message.chat.send_message(f"Куплено. -{l['price_diamonds']} алм.")
        try: await ctx.bot.send_message(l['seller_id'], f"Продано. +{earnings}$")
        except: pass
        return
    
    if action == "mkt_cards":
        cards = get_all_cards()
        if not cards: return await query.message.chat.send_message("Пусто.")
        kb = [[InlineKeyboardButton(c['name'], callback_data=f"mkt_card:{uid}:{c['id']}")] for c in cards]
        kb.append([InlineKeyboardButton("Назад", callback_data=f"mkt_back:{uid}")])
        await safe_del(query.message)
        await query.message.chat.send_message("Карта:", reply_markup=InlineKeyboardMarkup(kb))
        return
    
    if action == "mkt_card":
        card_id = int(parts[2])
        with db_lock:
            conn = db()
            try: card = conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
            finally: conn.close()
        if not card: return
        kb = []
        for rn, ri in RARITIES.items():
            with db_lock:
                conn = db()
                try: cnt = conn.execute("SELECT COUNT(*) FROM market_listings WHERE card_id=? AND rarity=?", (card_id, rn)).fetchone()[0]
                finally: conn.close()
            if cnt > 0: kb.append([InlineKeyboardButton(f"{ri['e']} {rn} ({cnt})", callback_data=f"mkt_rarity:{uid}:{card_id}:{rn}")])
        if not kb: kb.append([InlineKeyboardButton("Нет", callback_data="noop")])
        kb.append([InlineKeyboardButton("Назад", callback_data=f"mkt_cards:{uid}")])
        await safe_del(query.message)
        await query.message.chat.send_message(f"Редкости «{card['name']}»:", reply_markup=InlineKeyboardMarkup(kb))
        return
    
    if action == "mkt_rarity":
        card_id, rarity = int(parts[2]), parts[3]
        with db_lock:
            conn = db()
            try:
                listings = conn.execute("SELECT ml.*, u.name as sn, u.username as su FROM market_listings ml JOIN users u ON ml.seller_id=u.user_id WHERE ml.card_id=? AND ml.rarity=? ORDER BY ml.price_diamonds", (card_id, rarity)).fetchall()
                card = conn.execute("SELECT name FROM cards WHERE id=?", (card_id,)).fetchone()
            finally: conn.close()
        ri = RARITIES[rarity]
        kb = [[InlineKeyboardButton(f"{card['name']} {ri['e']}, {l['quantity']} шт. {l['price_diamonds']} алм.", callback_data=f"mkt_item:{uid}:{l['id']}")] for l in listings]
        if not kb: kb.append([InlineKeyboardButton("Нет", callback_data="noop")])
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
            finally: conn.close()
        kb = [[InlineKeyboardButton("Рынок", callback_data=f"mkt_new:{uid}:0")], [InlineKeyboardButton("По категориям", callback_data=f"mkt_cards:{uid}")], [InlineKeyboardButton("Обмен", callback_data=f"mkt_exchange:{uid}")]]
        await safe_del(query.message)
        await query.message.chat.send_message(f"Рынок. Всего: {total} | Ваших: {my}", reply_markup=InlineKeyboardMarkup(kb))
        return
    
    if action == "sell_pick":
        card_id = int(parts[2])
        with db_lock:
            conn = db()
            try: rows = conn.execute("SELECT rarity,quantity FROM user_cards WHERE user_id=? AND card_id=? AND quantity>0", (uid, card_id)).fetchall()
            finally: conn.close()
        kb = []
        for r in rows:
            if r['rarity'] != 'Эксклюзивный':
                ri = RARITIES.get(r['rarity'], {})
                kb.append([InlineKeyboardButton(f"{ri.get('e','')} {r['rarity']} ({r['quantity']} шт.)", callback_data=f"sell_rarity:{uid}:{card_id}:{r['rarity']}")])
        if not kb: kb.append([InlineKeyboardButton("Эксклюзив только через рынок", callback_data="noop")])
        kb.append([InlineKeyboardButton("Назад", callback_data=f"sell_back:{uid}")])
        await safe_del(query.message)
        await query.message.chat.send_message("Редкость:", reply_markup=InlineKeyboardMarkup(kb))
        return
    
    if action == "sell_rarity":
        card_id, rarity = int(parts[2]), parts[3]
        user_states[uid] = {'action': 'sell_qty', 'card_id': card_id, 'rarity': rarity}
        await safe_del(query.message)
        await query.message.chat.send_message("Количество:")
        return
    
    if action == "sell_do":
        card_id, rarity, qty, price = int(parts[2]), parts[3], int(parts[4]), int(parts[5])
        title = parts[6].replace(';', ':') if len(parts) > 6 else "Без названия"
        if not remove_user_card(uid, card_id, rarity, qty): return await query.answer("Недостаточно.", show_alert=True)
        with db_lock:
            conn = db()
            try:
                conn.execute("INSERT INTO market_listings (seller_id,card_id,rarity,quantity,price_diamonds,title) VALUES (?,?,?,?,?,?)", (uid, card_id, rarity, qty, price, title))
                conn.commit()
            finally: conn.close()
        await safe_del(query.message)
        await query.message.chat.send_message("Выставлено.")
        return
    
    if action == "sell_cancel":
        user_states.pop(uid, None)
        await safe_del(query.message)
        await query.message.chat.send_message("Отменено.")
        return
    
    if action == "sell_back":
        cards = get_user_cards(uid)
        seen = set()
        kb = [[InlineKeyboardButton(c['card_name'], callback_data=f"sell_pick:{uid}:{c['card_id']}")] for c in cards if c['card_id'] not in seen and not seen.add(c['card_id'])]
        kb.append([InlineKeyboardButton("Отмена", callback_data=f"sell_cancel:{uid}")])
        await safe_del(query.message)
        await query.message.chat.send_message("Карта:", reply_markup=InlineKeyboardMarkup(kb))
        return
    
    if action == "farm_toggle":
        u = get_user(uid)
        if not u['farm_card_id']: return await query.answer("Установите карту.", show_alert=True)
        new_status = 0 if u['farm_enabled'] else 1
        update_user(uid, 'farm_enabled', new_status)
        await query.answer(f"Ферма: {'Активна' if new_status else 'Неактивна'}")
        await safe_del(query.message)
        await farm_cmd(update, ctx)
        return
    
    if action == "farm_change":
        cards = get_user_cards(uid)
        if not cards: return await query.answer("Нет карт.", show_alert=True)
        kb = [[InlineKeyboardButton(f"{c['card_name']} {RARITIES[c['rarity']]['e']} ({FARM_INCOME.get(c['rarity'],0)}$/5мин)", callback_data=f"farm_set:{uid}:{c['card_id']}:{c['rarity']}")] for c in cards]
        kb.append([InlineKeyboardButton("Назад", callback_data=f"farm_back:{uid}")])
        await safe_del(query.message)
        await query.message.chat.send_message("Карта для фермы:", reply_markup=InlineKeyboardMarkup(kb))
        return
    
    if action == "farm_set":
        card_id, rarity = int(parts[2]), parts[3]
        update_user(uid, 'farm_card_id', card_id)
        update_user(uid, 'farm_rarity', rarity)
        with db_lock:
            conn = db()
            try: card = conn.execute("SELECT name FROM cards WHERE id=?", (card_id,)).fetchone()
            finally: conn.close()
        ri = RARITIES[rarity]
        income = FARM_INCOME.get(rarity, 0)
        await safe_del(query.message)
        await query.message.chat.send_message(f"Установлена «{card['name']}» {ri['e']}\nПрибыль: {income}$ каждые 5 минут")
        await farm_cmd(update, ctx)
        return
    
    if action == "farm_back":
        await farm_cmd(update, ctx)
        return
    
    if action == "sup_reply":
        ticket_id = int(parts[2])
        user_states[query.from_user.id] = {'action': 'admin_reply', 'ticket_id': ticket_id}
        await safe_del(query.message)
        await query.message.chat.send_message("Ответ:")
        return
    
    if action == "sup_answer":
        ticket_id = int(parts[2])
        user_states[uid] = {'action': 'user_reply_to_admin', 'ticket_id': ticket_id}
        await safe_del(query.message)
        await query.message.chat.send_message("Ответ поддержке:")
        return

# === ТОЧКА ВХОДА ===
async def post_init(app):
    asyncio.create_task(farm_loop(app))

def main():
    init_db()
    restore_from_firebase()
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