#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import random
import re
import math
import time
import threading
import atexit
import asyncio
import aiohttp
import traceback
from collections import Counter, defaultdict

import ftfy
from unidecode import unidecode
import emoji
from thefuzz import fuzz

try:
    from langdetect import detect, DetectorFactory
except Exception:
    detect = None
    DetectorFactory = None

try:
    from razdel import tokenize as razdel_tokenize
except Exception:
    razdel_tokenize = None

try:
    import markovify
except Exception:
    markovify = None

try:
    import pymorphy3 as pymorphy
except Exception:
    try:
        import pymorphy2 as pymorphy
    except Exception:
        pymorphy = None

try:
    from nltk.stem.snowball import SnowballStemmer
except Exception:
    SnowballStemmer = None

try:
    import stopwords as stopwords_lib
except Exception:
    stopwords_lib = None

try:
    from sentence_transformers import SentenceTransformer
    EMBED_MODEL = SentenceTransformer('cointegrated/rubert-tiny2')
except Exception:
    EMBED_MODEL = None

try:
    import faiss
    import numpy as np
    FAISS_AVAILABLE = True
except Exception:
    FAISS_AVAILABLE = False

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.cluster import KMeans
    SKLEARN_AVAILABLE = True
except Exception:
    SKLEARN_AVAILABLE = False

try:
    import networkx as nx
    NETWORKX_AVAILABLE = True
except Exception:
    NETWORKX_AVAILABLE = False

import telebot
from telebot import types

TOKEN = os.environ.get("BOT_TOKEN", "8445343788:AAHhxjWpxtGBghkF02nlr2FLBL3hnf9mXug")
BOT_TITLE = "ЛакерИИ"
MODEL_FILE = "laker_memory.json"
SETTINGS_FILE = "laker_settings.json"
OPENROUTER_KEY_FILE = "openrouter_key.txt"  # Файл для хранения ключа ИИ

# Загружаем ключ OpenRouter из файла (если есть)
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

SYSTEM_PROMPT = """Ты ЛакерИИ или просто Лакер. Твой создатель тоже Лакер.
Отвечай короткими фразами и копируй характер пользователя.
Отвечай матами, когда тебя обзывают.
Отвечай строго коротко и по факту. Не выдумывай чушь. Если не знаешь, так и скажи.
Категорически запрещено описывать действия в звёздочках или скобках (никаких *вздохнул* и т.д.)."""

ai_history = defaultdict(list)

MAX_PHRASES = 8000
MAX_PAIRS = 3000
MAX_TOKENS_PER_PHRASE = 80
SAVE_EVERY = 5
REACTION_CHANCE = 0.15
STICKER_CHANCE = 0.15
QUALITY_THRESHOLD = 1.0

SEP = "\u0001"
START = "__START__"
END = "__END__"
SENTENCE_END = {".", "!", "?", "…"}

TRIGGER_RE = re.compile(r'^\s*(лакер(?:у|а|ы)?|laker(?:у|а|ы)?)(?:[\s,:;.!?—–-]+|$)', re.IGNORECASE)
STICKER_RE = re.compile(r'стикер', re.IGNORECASE)

PURE_PUNCT = set(".,!?;:()[]{}<>«»\"'`~@#$%^&*+=|/—…-_")

LENGTH_PROFILES = {
    "short": {"min": 1, "max": 8},
    "medium": {"min": 2, "max": 16},
    "long": {"min": 4, "max": 28}
}

STYLE_PROFILES = {
    "precise": {"candidates": 15, "top_limit": 20, "phrase_seeds": 5, "keyword_generations": 5, "start_generations": 2, "markov_calls": 2, "noise": 0.15, "fuzz_weight": 4.0},
    "normal": {"candidates": 20, "top_limit": 30, "phrase_seeds": 6, "keyword_generations": 6, "start_generations": 2, "markov_calls": 3, "noise": 0.35, "fuzz_weight": 3.5},
    "wild": {"candidates": 25, "top_limit": 40, "phrase_seeds": 6, "keyword_generations": 8, "start_generations": 3, "markov_calls": 5, "noise": 0.85, "fuzz_weight": 3.0}
}

LENGTH_NAMES = {"short": "короткий", "medium": "средний", "long": "длинный"}
STYLE_NAMES = {"precise": "точный", "normal": "обычный", "wild": "безумный"}
CHANNEL_NAMES = {"all": "все посты", "trigger": "только триггер", "off": "выкл"}

DEFAULT_SETTINGS = {"length": "medium", "style": "normal", "reactions": "on", "channel": "all", "ai_mode": "on", "model": "deepseek"}

ALLOWED_REACTIONS = ["🤨", "", "😏", "😂", "👍", "", "💩", "🤯", "👌", "😅"]
REACTION_FEEDBACK = {"": 2, "👌": 1, "❤️": 3, "": 3, "😂": 2, "🤩": 2, "": 1, "🤨": -1, "🤔": 0, "": 1, "😅": -1, "": -1, "👎": -3, "💩": -3, "": -3, "🤬": -3}

INTENT_PATTERNS = {
    "greeting": re.compile(r'\b(привет|приветик|здравствуй|здравствуйте|здорово|здарова|салют|hello|hi|хай|ку)\b', re.I),
    "farewell": re.compile(r'\b(пока|прощай|до\s*встречи|спокойной\s*ночи|бб|bb)\b', re.I),
    "howareyou": re.compile(r'(как\s+(дела|ты|жизнь|настроение)|чё\s+как|что\s+как)', re.I),
    "whoareyou": re.compile(r'(кто\s+ты|ты\s+кто|ты\s+бот|ты\s+нейросеть|ты\s+ии)', re.I),
    "whatdoing": re.compile(r'(что\s+делаешь|чё\s+делаешь|чем\s+занимаешься|что\s+пишешь)', re.I),
    "love": re.compile(r'\b(люблю|нравится|обожаю|кайф)\b', re.I),
    "insult": re.compile(r'\b(тупой|дебил|идиот|лох|дурак|глупый|бестолковый)\b', re.I),
    "praise": re.compile(r'\b(умный|хороший|молодец|крутой|лучший|красава|гений)\b', re.I),
    "laugh": re.compile(r'(хах|ахах|лол|lol|\)\)\)+|😂|🤣)', re.I),
    "help": re.compile(r'\b(помощь|help|команды|что\s+ты\s+умеешь)\b', re.I),
    "thanks": re.compile(r'\b(спасибо|благодарю|спс|сяб)\b', re.I),
    "sorry": re.compile(r'\b(извини|прости|сори|sorry|виноват)\b', re.I),
    "question": re.compile(r'(почему|зачем|когда|где|кто|что|как|какой|сколько|можно|будет)', re.I),
}

INTENT_ORDER = ["greeting", "farewell", "howareyou", "whoareyou", "whatdoing", "thanks", "sorry", "praise", "insult", "laugh", "love", "help", "question"]

INTENT_TEMPLATES = {
    "greeting": ["привет", "здарова", "ку"],
    "farewell": ["пока", "до встречи", "бб"],
    "howareyou": ["нормально", "дела нормально", "учусь потихоньку"],
    "whoareyou": ["я {bot}", "я бот, который учится"],
    "whatdoing": ["отвечаю тебе", "учусь", "думаю"],
    "thanks": ["пожалуйста", "не за что"],
    "sorry": ["ладно", "ок"],
    "praise": ["спасибо", "стараюсь"],
    "insult": ["сам такой", "не груби"],
    "laugh": ["ахах", "лол", "😂"],
    "help": ["пиши Лакер", "есть /settings"],
    "question": ["хороший вопрос", "думаю"]
}

NAME_RE = re.compile(r'меня\s+зовут\s+([а-яёa-z0-9_\-]+)', re.I)
LIKE_RE = re.compile(r'(?:люблю|нравится|обожаю)\s+([а-яёa-z0-9_\-]+(?:\s+[а-яёa-z0-9_\-]+)?)', re.I)

if DetectorFactory is not None:
    try: DetectorFactory.seed = 0
    except Exception: pass

bot = telebot.TeleBot(TOKEN, parse_mode=None)
bot_id = None
bot_username = None

model_lock = threading.Lock()
settings_lock = threading.Lock()

model = {
    "phrases": [], "transitions": {}, "word_transitions": {}, "df": {}, "clusters": {},
    "pairs": [], "good_texts": [], "bad_texts": [], "bot_messages": {},
    "recent_answers": {}, "facts": {}, "recent_context": {}, "stickers": [],
    "embeddings": [], "knowledge_graph": [],
    "meta": {"learned": 0}
}

inverted = {}
known_texts_lower = set()
good_texts_lower = set()
bad_texts_lower = set()
settings = {}
learn_since_save = 0
markov_model = None
markov_built_count = 0
lemma_cache = {}
analysis_cache = {}

faiss_index = None
faiss_phrases = []
knowledge_graph = None
tfidf_vectorizer = None
tfidf_matrix = None

# Защита от двойных сообщений
_processed_msgs = set()
def is_duplicate(message):
    mid = message.message_id
    if mid in _processed_msgs:
        return True
    _processed_msgs.add(mid)
    if len(_processed_msgs) > 2000:
        _processed_msgs.clear()
    return False

# Состояние для смены ключа OpenRouter
key_change_state = {}
KEY_PASSWORD = "eee345678b"

# === NLP УТИЛИТЫ ===
STOPWORDS = set()
if stopwords_lib is not None:
    for lang in ("russian", "english"):
        try: STOPWORDS.update(stopwords_lib.words(lang))
        except Exception: pass
STOPWORDS.update({"и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а", "то", "все", "она", "так", "его", "но", "да", "ты", "к", "у", "же", "вы", "за", "бы", "по", "только", "её", "мне", "было", "вот", "от", "меня", "ещё", "нет", "о", "из", "ему", "тебя", "быть", "может", "если", "уже", "до", "или", "ни", "когда", "даже", "нам", "раз", "уж", "ли", "ну", "всё", "мы", "они", "оно", "нас", "вас", "их", "мой", "моя", "моё", "твой", "наш", "ваш", "свой", "чужой", "это", "этот", "эта", "эти", "тут", "там", "очень", "просто", "будет", "был", "была", "были", "есть", "нет"})

try: morph = pymorphy.MorphAnalyzer() if pymorphy is not None else None
except Exception: morph = None
try: stemmer = SnowballStemmer("russian") if SnowballStemmer is not None else None
except Exception: stemmer = None

def lemmatize_word(word):
    w = word.lower().strip()
    if not w: return ""
    if w in lemma_cache: return lemma_cache[w]
    if not re.search(r"[а-яё]", w, re.I):
        res = w[:-1] if w.endswith("s") and len(w) > 3 else w
        if len(lemma_cache) > 5000: lemma_cache.clear()
        lemma_cache[w] = res
        return res
    res = w
    if morph is not None:
        try:
            p = morph.parse(w)[0]
            if p.normal_form: res = p.normal_form.lower()
            if getattr(p, "score", 1) < 0.35 and stemmer is not None:
                try: res = stemmer.stem(w)
                except Exception: pass
        except Exception:
            if stemmer is not None:
                try: res = stemmer.stem(w)
                except Exception: res = w
    elif stemmer is not None:
        try: res = stemmer.stem(w)
        except Exception: res = w
    if len(lemma_cache) > 5000: lemma_cache.clear()
    lemma_cache[w] = res
    return res

def has_emoji(text):
    if not text: return False
    try: return bool(emoji.emoji_list(text))
    except Exception:
        try: return any(emoji.is_emoji(ch) for ch in text)
        except Exception: return False

def raw_tokenize(text):
    if razdel_tokenize is not None:
        try: return [t.text for t in razdel_tokenize(text)]
        except Exception: pass
    return text.split()

def preprocess_text(text):
    if not text: return ""
    try: text = ftfy.fix_text(text)
    except Exception: pass
    text = text.replace("\r", " ").replace("\n", " ")
    return text.strip()

def is_good_token(tok):
    if not tok or not tok.strip(): return False
    if tok in SENTENCE_END: return True
    if tok in PURE_PUNCT: return False
    if has_emoji(tok): return True
    if any(ch.isalnum() for ch in tok): return True
    return False

def detect_lang(text):
    if not text or len(text.strip()) < 3: return "unknown"
    if re.search(r"[а-яё]", text, re.I): return "ru"
    if re.search(r"[a-z]", text, re.I): return "en"
    if detect is None: return "unknown"
    try: return detect(text[:200])
    except Exception: return "unknown"

def fuzzy_norm(text):
    if not text: return ""
    t = text.lower()
    if re.search(r"[а-яё]", t, re.I): return t
    try: return unidecode(t)
    except Exception: return t

def get_tokens_and_lemmas(text):
    if not text: return [], []
    key = text if len(text) <= 500 else text[:500] + str(len(text))
    if key in analysis_cache: return analysis_cache[key]
    text = preprocess_text(text)
    tokens, lemmas = [], []
    if text:
        raw = raw_tokenize(text)
        for tok in raw:
            if len(tokens) >= MAX_TOKENS_PER_PHRASE: break
            if not is_good_token(tok): continue
            t = tok.lower().strip()
            if not t: continue
            if t in (START, END) or SEP in t: t = t.strip("_") or "слово"
            tokens.append(t)
            if t in SENTENCE_END: continue
            if has_emoji(t):
                if t not in STOPWORDS: lemmas.append(t)
            elif t.isalpha():
                if t not in STOPWORDS: lemmas.append(lemmatize_word(t))
            else:
                if t not in STOPWORDS: lemmas.append(t)
    if len(analysis_cache) > 2000: analysis_cache.clear()
    analysis_cache[key] = (tokens, lemmas)
    return tokens, lemmas

def parse_trigger(text):
    if not text: return False, ""
    m = TRIGGER_RE.match(text)
    if m: return True, text[m.end():].strip()
    return False, ""

def is_bot_mentioned(message):
    if not bot_username: return False
    text = message.text or ""
    if not text: return False
    uname = "@" + bot_username
    uname_lower = uname.lower()
    if uname_lower in text.lower(): return True
    if uname.lstrip("@").lower() in text.lower(): return True
    entities = message.entities or []
    for ent in entities:
        if getattr(ent, "type", "") == "mention":
            mention_text = text[ent.offset:ent.offset + ent.length].lower()
            if mention_text == uname_lower or mention_text == uname.lstrip("@").lower(): return True
    return False

def detect_intent(text):
    if not text: return None
    for intent in INTENT_ORDER:
        pattern = INTENT_PATTERNS.get(intent)
        if pattern is not None:
            try:
                if pattern.search(text): return intent
            except Exception: pass
    return None

def format_template(tmpl):
    try: return tmpl.format(bot=BOT_TITLE)
    except Exception: return tmpl

# === СЕМАНТИЧЕСКИЙ ПОИСК ===
def get_embedding(text):
    if EMBED_MODEL is None or not text: return None
    try:
        embedding = EMBED_MODEL.encode(text, show_progress_bar=False)
        return embedding.astype('float32')
    except Exception:
        return None

def rebuild_faiss_index():
    global faiss_index, faiss_phrases
    if not FAISS_AVAILABLE or EMBED_MODEL is None: return
    phrases_with_text = [(i, p.get("text", "")) for i, p in enumerate(model["phrases"]) if p.get("text")]
    if not phrases_with_text:
        faiss_index = None
        faiss_phrases = []
        return
    texts = [text for _, text in phrases_with_text]
    indices = [idx for idx, _ in phrases_with_text]
    try:
        embeddings = EMBED_MODEL.encode(texts, show_progress_bar=False, batch_size=32).astype('float32')
        dimension = embeddings.shape[1]
        faiss_index = faiss.IndexFlatIP(dimension)
        faiss.normalize_L2(embeddings)
        faiss_index.add(embeddings)
        faiss_phrases = indices
    except Exception:
        faiss_index = None
        faiss_phrases = []

def semantic_search(query_text, top_k=10):
    if faiss_index is None or EMBED_MODEL is None or not query_text: return []
    try:
        query_embedding = EMBED_MODEL.encode([query_text], show_progress_bar=False).astype('float32')
        faiss.normalize_L2(query_embedding)
        k = min(top_k, len(faiss_phrases))
        if k == 0: return []
        distances, indices = faiss_index.search(query_embedding, k)
        results = []
        for i, idx in enumerate(indices[0]):
            if idx < len(faiss_phrases):
                results.append((faiss_phrases[idx], float(distances[0][i])))
        return results
    except Exception:
        return []

# === ГРАФ ЗНАНИЙ ===
def build_knowledge_graph():
    global knowledge_graph
    if not NETWORKX_AVAILABLE: return
    knowledge_graph = nx.Graph()
    for phrase in model["phrases"][-1000:]:
        text = phrase.get("text", "")
        tokens, lemmas = get_tokens_and_lemmas(text)
        meaningful = [l for l in lemmas if l not in STOPWORDS and len(l) > 3]
        for i, word1 in enumerate(meaningful):
            knowledge_graph.add_node(word1)
            for word2 in meaningful[i+1:i+3]:
                knowledge_graph.add_node(word2)
                if knowledge_graph.has_edge(word1, word2):
                    knowledge_graph[word1][word2]['weight'] += 1
                else:
                    knowledge_graph.add_edge(word1, word2, weight=1)

def get_related_words(word, depth=2):
    if knowledge_graph is None or word not in knowledge_graph: return []
    related = []
    try:
        neighbors = list(nx.neighbors(knowledge_graph, word))
        for neighbor in neighbors[:5]:
            related.append(neighbor)
            if depth > 1:
                for second_neighbor in list(nx.neighbors(knowledge_graph, neighbor))[:3]:
                    if second_neighbor != word: related.append(second_neighbor)
    except Exception: pass
    return list(set(related))[:10]

# === TF-IDF КЛАСТЕРИЗАЦИЯ ===
def build_tfidf_clusters():
    global tfidf_vectorizer, tfidf_matrix
    if not SKLEARN_AVAILABLE: return
    texts = [p.get("text", "") for p in model["phrases"] if p.get("text")]
    if len(texts) < 10: return
    try:
        tfidf_vectorizer = TfidfVectorizer(max_features=1000, stop_words=list(STOPWORDS)[:100])
        tfidf_matrix = tfidf_vectorizer.fit_transform(texts)
        n_clusters = min(10, len(texts) // 5)
        if n_clusters > 1:
            kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
            clusters = kmeans.fit_predict(tfidf_matrix)
            model["clusters"] = {}
            for i, cluster_id in enumerate(clusters):
                model["clusters"].setdefault(str(cluster_id), []).append(i)
    except Exception: pass

# === УПРАВЛЕНИЕ МОДЕЛЬЮ ===
def extract_topic_tags(lemmas):
    return [l for l in lemmas if l not in STOPWORDS and len(l) > 3 and l.isalpha()][:2]

def rebuild_df_and_index():
    inverted.clear()
    model["df"] = {}
    for idx, phrase in enumerate(model["phrases"]):
        for lemma in set(phrase.get("lemmas", [])):
            model["df"][lemma] = model["df"].get(lemma, 0) + 1
            inverted.setdefault(lemma, set()).add(idx)

def rebuild_known_texts():
    global known_texts_lower
    known_texts_lower = set()
    for p in model["phrases"]:
        t = p.get("text", "")
        if t: known_texts_lower.add(t.strip().lower())

def rebuild_good_bad_sets():
    global good_texts_lower, bad_texts_lower
    good_texts_lower = {t.lower() for t in model.get("good_texts", []) if isinstance(t, str)}
    bad_texts_lower = {t.lower() for t in model.get("bad_texts", []) if isinstance(t, str)}

def learn_transitions(tokens):
    if not tokens: return
    prev2, prev1 = START, START
    for token in tokens + [END]:
        key = f"{prev2}{SEP}{prev1}"
        nxt = model["transitions"].setdefault(key, {})
        nxt[token] = nxt.get(token, 0) + 1
        prev2, prev1 = prev1, token

def learn_word_transitions(tokens):
    if not tokens: return
    wt = model.setdefault("word_transitions", {})
    prev = START
    for token in tokens + [END]:
        nxt = wt.setdefault(prev, {})
        nxt[token] = nxt.get(token, 0) + 1
        prev = token

def rebuild_all_from_phrases():
    rebuild_df_and_index()
    rebuild_known_texts()
    model["transitions"] = {}
    model["word_transitions"] = {}
    for phrase in model["phrases"]:
        learn_transitions(phrase.get("tokens", []))
        learn_word_transitions(phrase.get("tokens", []))

def load_model():
    global model
    data = load_json(MODEL_FILE, {})
    if not isinstance(data, dict): data = {}
    model["phrases"] = [p for p in data.get("phrases", []) if isinstance(p, dict)]
    model["transitions"] = {str(k): v for k, v in data.get("transitions", {}).items() if isinstance(v, dict)}
    model["word_transitions"] = data.get("word_transitions", {}) if isinstance(data.get("word_transitions"), dict) else {}
    model["df"] = data.get("df", {}) if isinstance(data.get("df"), dict) else {}
    model["clusters"] = data.get("clusters", {}) if isinstance(data.get("clusters"), dict) else {}
    model["pairs"] = [p for p in data.get("pairs", []) if isinstance(p, dict)]
    model["good_texts"] = [t for t in data.get("good_texts", []) if isinstance(t, str)]
    model["bad_texts"] = [t for t in data.get("bad_texts", []) if isinstance(t, str)]
    model["bot_messages"] = data.get("bot_messages", {}) if isinstance(data.get("bot_messages"), dict) else {}
    model["recent_answers"] = data.get("recent_answers", {}) if isinstance(data.get("recent_answers"), dict) else {}
    model["facts"] = data.get("facts", {}) if isinstance(data.get("facts"), dict) else {}
    model["recent_context"] = data.get("recent_context", {}) if isinstance(data.get("recent_context"), dict) else {}
    model["stickers"] = data.get("stickers", []) if isinstance(data.get("stickers"), list) else []
    model["embeddings"] = data.get("embeddings", []) if isinstance(data.get("embeddings"), list) else []
    model["knowledge_graph"] = data.get("knowledge_graph", []) if isinstance(data.get("knowledge_graph"), list) else []
    model["meta"] = data.get("meta", {"learned": 0}) if isinstance(data.get("meta"), dict) else {"learned": 0}
    rebuild_all_from_phrases()
    rebuild_known_texts()
    rebuild_good_bad_sets()

def should_learn_text(text, tokens, is_dialog=False):
    if not text or not tokens: return False
    if text.startswith("/"): return False
    if re.search(r"https?://", text, re.I): return False
    word_tokens = [t for t in tokens if t not in SENTENCE_END]
    if not word_tokens: return False
    if not is_dialog and len(word_tokens) < 2: return False
    if is_dialog and len(word_tokens) < 1: return False
    if len(word_tokens) == 1 and word_tokens[0] in STOPWORDS: return False
    compact = re.sub(r"\s+", "", text.lower())
    if len(compact) > 4 and len(set(compact)) <= 2: return False
    return True

def add_phrase(text, is_dialog=False, force=False):
    global learn_since_save
    if not text or not text.strip(): return False
    text = preprocess_text(text)
    lower = text.lower()
    tokens, lemmas = get_tokens_and_lemmas(text)
    if not tokens: return False
    if lower in known_texts_lower: return False
    if lower in bad_texts_lower: return False
    if not force and not should_learn_text(text, tokens, is_dialog=is_dialog): return False
    
    tags = extract_topic_tags(lemmas)
    phrase = {"text": text.strip(), "tokens": tokens, "lemmas": lemmas, "tags": tags, "lang": detect_lang(text), "ts": time.time()}
    
    model["phrases"].append(phrase)
    if len(model["phrases"]) > MAX_PHRASES:
        model["phrases"] = model["phrases"][-MAX_PHRASES:]
        rebuild_all_from_phrases()
    else:
        idx = len(model["phrases"]) - 1
        for lemma in set(lemmas):
            model["df"][lemma] = model["df"].get(lemma, 0) + 1
            inverted.setdefault(lemma, set()).add(idx)
        known_texts_lower.add(lower)
        learn_transitions(tokens)
        learn_word_transitions(tokens)
    
    model["meta"]["learned"] = int(model["meta"].get("learned", 0)) + 1
    learn_since_save += 1
    return True

def add_pair(context_text, response_text):
    if not context_text or not response_text: return False
    _, context_lemmas = get_tokens_and_lemmas(context_text)
    ctx_lower = context_text.lower().strip()
    for p in model["pairs"]:
        if fuzz.ratio(ctx_lower, p["context"].lower().strip()) > 90: return False
    model["pairs"].append({"context": context_text.strip(), "response": response_text.strip(), "lemmas": context_lemmas})
    if len(model["pairs"]) > MAX_PAIRS: model["pairs"] = model["pairs"][-MAX_PAIRS:]
    return True

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

def apply_positive_feedback(text, weight=2):
    if not text: return False
    text = preprocess_text(text)
    lower = text.lower()
    if lower in bad_texts_lower:
        bad_texts_lower.discard(lower)
        model["bad_texts"] = [t for t in model.get("bad_texts", []) if t.lower() != lower]
    good_texts_lower.add(lower)
    model.setdefault("good_texts", []).append(text)
    if len(model["good_texts"]) > 1000: model["good_texts"] = model["good_texts"][-1000:]
    if weight >= 2: add_phrase(text, is_dialog=True, force=True)
    return True

def apply_negative_feedback(text, weight=-1):
    if not text: return False
    text = preprocess_text(text)
    lower = text.lower()
    bad_texts_lower.add(lower)
    model.setdefault("bad_texts", []).append(text)
    if len(model["bad_texts"]) > 1000: model["bad_texts"] = model["bad_texts"][-1000:]
    if lower in good_texts_lower:
        good_texts_lower.discard(lower)
        model["good_texts"] = [t for t in model.get("good_texts", []) if t.lower() != lower]
    if weight <= -3 and lower in known_texts_lower:
        model["phrases"] = [p for p in model["phrases"] if p.get("text", "").lower() != lower]
        rebuild_all_from_phrases()
    return True

def rebuild_markov(force=False):
    global markov_model, markov_built_count
    if markovify is None:
        markov_model = None
        return
    total = len(model["phrases"])
    if not force and abs(total - markov_built_count) < 10: return
    if total < 5:
        markov_model = None
        markov_built_count = total
        return
    texts = [p.get("text", "").replace("\n", " ") for p in model["phrases"][-800:] if p.get("text")]
    if len(texts) < 5: markov_model = None
    else:
        corpus = "\n".join(texts)
        state_size = 2 if len(texts) >= 50 else 1
        try: markov_model = markovify.NewlineText(corpus, state_size=state_size)
        except Exception:
            try: markov_model = markovify.NewlineText(corpus, state_size=1)
            except Exception: markov_model = None
    markov_built_count = total

def maybe_rebuild_markov():
    if markovify is None: return
    if abs(len(model["phrases"]) - markov_built_count) >= 10: rebuild_markov(force=True)

def generate_markovify_sentence(profile):
    if markov_model is None: return None
    try:
        if profile["max"] <= 10: return markov_model.make_short_sentence(max_chars=max(20, profile["max"] * 14), tries=25)
        else: return markov_model.make_sentence(tries=30, max_overlap_ratio=0.7)
    except Exception: return None

def retrieve_phrases(query_lemmas, query_text, limit=20):
    if not model["phrases"]: return []
    scores = {}
    n = len(model["phrases"])
    
    if query_text and FAISS_AVAILABLE and EMBED_MODEL is not None:
        semantic_results = semantic_search(query_text, top_k=limit * 2)
        for idx, score in semantic_results:
            scores[idx] = scores.get(idx, 0.0) + score * 10.0
    
    for lemma in set(query_lemmas):
        try: df = int(model["df"].get(lemma, 0))
        except Exception: df = 0
        if df <= 0: continue
        idf = math.log((n + 1) / (df + 1)) + 1.0
        for idx in inverted.get(lemma, set()):
            scores[idx] = scores.get(idx, 0.0) + idf

    if len(scores) < limit and query_text:
        qf = fuzzy_norm(query_text)
        start = max(0, len(model["phrases"]) - 200)
        for idx in range(start, len(model["phrases"])):
            if idx in scores: continue
            try:
                r = fuzz.token_set_ratio(qf, fuzzy_norm(model["phrases"][idx].get("text", ""))) / 100.0
            except Exception: r = 0.0
            if r > 0.4: scores[idx] = scores.get(idx, 0.0) + r * 3.0
            
    if not scores:
        sample_size = min(limit, len(model["phrases"]))
        if sample_size <= 0: return []
        return [(idx, 0.1) for idx in random.sample(range(len(model["phrases"])), sample_size)]
    items = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return items[:limit]

def get_recent_answers(chat_id): return model.get("recent_answers", {}).get(str(chat_id), [])

def add_recent_answer(chat_id, answer):
    ra = model.setdefault("recent_answers", {})
    key = str(chat_id)
    lst = ra.setdefault(key, [])
    lst.append(answer.lower())
    if len(lst) > 10: lst[:] = lst[-10:]

def weighted_choice(counter):
    words = list(counter.keys())
    weights = [max(1, int(counter.get(w, 1))) for w in words]
    return random.choices(words, weights=weights, k=1)[0]

def choose_token(counter, current_len=0, min_len=0):
    if not counter: return None
    if current_len < min_len:
        non_end = {k: v for k, v in counter.items() if k != END and k not in SENTENCE_END}
        if non_end: counter = non_end
    return weighted_choice(counter)

def continue_from_context(seed_tokens, max_words, min_words):
    words = list(seed_tokens)[:max_words]
    if not words: return words
    if len(words) >= 2: prev2, prev1 = words[-2], words[-1]
    elif len(words) == 1: prev2, prev1 = START, words[-1]
    else: prev2, prev1 = START, START
    wt = model.get("word_transitions", {})
    loops = 0
    while len(words) < max_words and loops < max_words * 3:
        loops += 1
        token = None
        key = f"{prev2}{SEP}{prev1}"
        nxt = model["transitions"].get(key)
        if nxt: token = choose_token(nxt, len(words), min_words)
        if token is None or ((token == END or token in SENTENCE_END) and len(words) < min_words):
            nxt2 = wt.get(prev1)
            if nxt2: token = choose_token(nxt2, len(words), min_words)
        if token is None or ((token == END or token in SENTENCE_END) and len(words) < min_words):
            nxt_start = wt.get(START)
            if nxt_start: token = choose_token(nxt_start, len(words), min_words)
        if token is None or token == END:
            if len(words) >= min_words: break
            if model["phrases"]:
                p = random.choice(model["phrases"][-200:])
                extra = p.get("tokens", [])[:max(1, min_words - len(words))]
                if extra:
                    words.extend(extra)
                    words = words[:max_words]
                    if len(words) >= 2: prev2, prev1 = words[-2], words[-1]
                    elif len(words) == 1: prev2, prev1 = START, words[-1]
                    continue
            break
        if token == START: break
        words.append(token)
        if token in SENTENCE_END and (len(words) >= min_words or len(words) >= 2): break
        prev2, prev1 = prev1, token
    return words[:max_words]

def find_best_token_index(tokens, prompt_tokens, prompt_lemmas):
    prompt_token_set = set(prompt_tokens)
    prompt_lemma_set = set(prompt_lemmas)
    best_i, best_score = 0, -1.0
    for i, t in enumerate(tokens):
        score = 0.0
        if t in SENTENCE_END: score -= 1.0
        if t in prompt_token_set: score += 2.0
        if t.isalpha() and t not in STOPWORDS:
            try:
                if lemmatize_word(t) in prompt_lemma_set: score += 1.5
            except Exception: pass
        if t in STOPWORDS: score -= 0.3
        if score > best_score: best_score, best_i = score, i
    if best_score <= 0: return random.randint(0, max(0, len(tokens) - 1))
    return best_i

def generate_from_phrase_seed(phrase, prompt_tokens, prompt_lemmas, profile):
    tokens = phrase.get("tokens", [])
    if not tokens: return None
    idx = find_best_token_index(tokens, prompt_tokens, prompt_lemmas)
    start = max(0, idx - 1)
    context = tokens[start:start + 2]
    if not context: context = tokens[:1]
    if not context: return None
    return tokens_to_text(continue_from_context(context, profile["max"], profile["min"]))

def generate_from_keyword(prompt_tokens, profile):
    wt = model.get("word_transitions", {})
    usable = [t for t in prompt_tokens if t not in STOPWORDS and t not in SENTENCE_END and any(k != END and k not in SENTENCE_END for k in wt.get(t, {}))]
    if not usable:
        usable = [t for t in prompt_tokens if t not in SENTENCE_END and any(k != END and k not in SENTENCE_END for k in wt.get(t, {}))]
    if not usable: return None
    return tokens_to_text(continue_from_context([random.choice(usable)], profile["max"], profile["min"]))

def generate_from_start(profile):
    wt = model.get("word_transitions", {})
    start_counter = wt.get(START)
    if start_counter:
        token = choose_token(start_counter, 0, profile["min"])
        if token and token != END:
            return tokens_to_text(continue_from_context([token], profile["max"], profile["min"]))
    if model["phrases"]:
        p = random.choice(model["phrases"][-200:])
        tokens = p.get("tokens", [])
        if tokens: return tokens_to_text(continue_from_context(tokens[:1], profile["max"], profile["min"]))
    return None

def generate_fused_phrase(profile):
    if len(model["phrases"]) < 3: return None
    p1, p2, p3 = random.sample(model["phrases"][-300:], 3)
    t1, t2, t3 = p1.get("tokens", []), p2.get("tokens", []), p3.get("tokens", [])
    if not t1 or not t2: return None
    fusion = []
    fusion.extend(t1[:max(1, len(t1)//3)])
    fusion.extend(t2[max(1, len(t2)//3):max(2, len(t2)*2//3)])
    fusion.extend(t3[max(1, len(t3)*2//3):])
    fusion = fusion[:profile["max"]]
    return tokens_to_text(fusion)

def tokens_to_text(tokens):
    if not tokens: return ""
    text = ""
    for t in tokens:
        t = str(t)
        if t in SENTENCE_END: text += t
        else:
            if text and not text.endswith(" ") and text[-1] not in SENTENCE_END: text += " "
            elif text and text[-1] in SENTENCE_END: text += " "
            text += t
    for i, ch in enumerate(text):
        if ch.isalpha():
            text = text[:i] + ch.upper() + text[i + 1:]
            break
    return text.strip()

def truncate_text_to_words(text, max_words):
    if not text: return ""
    tokens, _ = get_tokens_and_lemmas(text)
    if len(tokens) > max_words: tokens = tokens[:max_words]
    return tokens_to_text(tokens)

def get_profile(chat_settings):
    length_key = chat_settings.get("length", "medium")
    style_key = chat_settings.get("style", "normal")
    lp = LENGTH_PROFILES.get(length_key, LENGTH_PROFILES["medium"]).copy()
    sp = STYLE_PROFILES.get(style_key, STYLE_PROFILES["normal"]).copy()
    lp.update(sp)
    return lp

def candidate_is_relevant(candidate, prompt_tokens, prompt_lemmas, intent):
    if not prompt_tokens and not prompt_lemmas: return True
    cand_tokens, cand_lemmas = get_tokens_and_lemmas(candidate)
    if set(prompt_lemmas) & set(cand_lemmas): return True
    if set(prompt_tokens) & set(cand_tokens): return True
    if intent in ("whoareyou", "whatdoing", "help", "thanks", "sorry", "farewell", "greeting"): return True
    try:
        if fuzz.partial_ratio(fuzzy_norm(" ".join(prompt_tokens)), fuzzy_norm(candidate)) > 65: return True
    except Exception: pass
    return False

def add_context(chat_id, text):
    if not text: return
    text = preprocess_text(text)
    if not text: return
    rc = model.setdefault("recent_context", {})
    key = str(chat_id)
    lst = rc.setdefault(key, [])
    lst.append({"text": text, "tags": extract_topic_tags(get_tokens_and_lemmas(text)[1])})
    if len(lst) > 10: lst[:] = lst[-10:]

def expand_query_with_context(chat_id, query):
    ctx = model.get("recent_context", {}).get(str(chat_id), [])[-2:]
    parts = [query] if query else []
    parts.extend([c["text"] for c in ctx])
    return " ".join([p for p in parts if p]).strip()

def extract_facts(chat_id, text):
    if not text: return
    text = preprocess_text(text)
    if not text: return
    facts = model.setdefault("facts", {})
    f = facts.setdefault(str(chat_id), {"likes": []})
    m = NAME_RE.search(text)
    if m: f["name"] = m.group(1).strip().capitalize()
    m = LIKE_RE.search(text)
    if m:
        like = m.group(1).strip().lower()
        likes = f.setdefault("likes", [])
        if like and like not in likes:
            likes.append(like)
            if len(likes) > 20: likes[:] = likes[-20:]

def get_fact_candidates(chat_id, text):
    f = model.get("facts", {}).get(str(chat_id), {})
    name = f.get("name")
    likes = f.get("likes", [])
    lower = (text or "").lower()
    out = []
    if name:
        if re.search(r'как\s+меня\s+зовут|кто\s+я|мо[её]\s+имя', lower): out.append(f"Тебя зовут {name}.")
        if re.search(r'привет|здарова|здравствуйте|ку', lower): out.append(f"Привет, {name}.")
    if likes:
        if re.search(r'что\s+я\s+люблю|что\s+мне\s+нравится', lower): out.append(f"Тебе нравится {likes[-1]}.")
        if re.search(r'что\s+ты\s+знаешь\s+обо\s+мне', lower):
            if name: out.append(f"Я помню: тебя зовут {name}.")
            else: out.append(f"Я помню: тебе нравится {likes[-1]}.")
    return out

def polish_answer(text, intent=None):
    if not text: return ""
    text = preprocess_text(text)
    text = re.sub(r'\s+', ' ', text).strip()
    if not text: return ""
    if text[-1].isalnum():
        if intent == "question" and re.search(r'\b(почему|зачем|как|что|где|когда|кто)\b', text, re.I): text += "?"
        else: text += "."
    return text

def minimal_answer(prompt, intent=None):
    if intent and INTENT_TEMPLATES.get(intent): return polish_answer(format_template(random.choice(INTENT_TEMPLATES[intent])), intent)
    tokens, _ = get_tokens_and_lemmas(prompt or "")
    words = [t for t in tokens if t not in STOPWORDS and t not in SENTENCE_END and len(t) > 1]
    if words: return random.choice(words).capitalize() + "."
    return random.choice(["", "🤔", "👌", ""])

# === AI ФУНКЦИИ ===
async def fetch_openrouter(session, messages, model_name):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY.strip()}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://telegram.org",
        "X-Title": "LakerAI Bot"
    }
    data = {
        "model": model_name,
        "messages": messages,
        "max_tokens": 1024,
        "stream": False
    }
    async with session.post(
        "https://openrouter.ai/api/v1/chat/completions",
        json=data,
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=120)
    ) as response:
        result = await response.json()
        if response.status != 200:
            error_msg = result.get("error", {}).get("message", str(result))
            raise Exception(f"OpenRouter Error ({response.status}): {error_msg}")
        return result["choices"][0]["message"]["content"]

async def fetch_groq(session, messages, model_name):
    if not GROQ_API_KEY:
        raise Exception("GROQ_API_KEY не установлен")
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY.strip()}",
        "Content-Type": "application/json"
    }
    data = {
        "model": model_name,
        "messages": messages,
        "max_tokens": 1024
    }
    async with session.post(
        "https://api.groq.com/openai/v1/chat/completions",
        json=data,
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=120)
    ) as response:
        result = await response.json()
        if response.status != 200:
            error_msg = result.get("error", {}).get("message", str(result))
            raise Exception(f"Groq Error ({response.status}): {error_msg}")
        return result["choices"][0]["message"]["content"]

async def ask_ai(user_id: int, user_name: str, text: str, selected_model: str = "deepseek") -> str:
    user_data_str = f"[Профиль: Имя={user_name}, ID={user_id}]"
    ai_history[user_id].append({
        "role": "user",
        "content": f"{user_data_str}\nСообщение: {text}"
    })
    ai_history[user_id] = ai_history[user_id][-MAX_AI_HISTORY:]

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + ai_history[user_id]

    async with aiohttp.ClientSession() as session:
        try:
            if selected_model == "openrouter":
                answer = await fetch_openrouter(session, messages, "meta-llama/llama-3.3-70b-instruct")
            elif selected_model == "groq":
                answer = await fetch_groq(session, messages, "llama-3.3-70b-versatile")
            elif selected_model == "deepseek":
                answer = await fetch_openrouter(session, messages, "deepseek/deepseek-chat")
            else:
                try:
                    answer = await fetch_openrouter(session, messages, "meta-llama/llama-3.3-70b-instruct")
                except Exception:
                    answer = await fetch_groq(session, messages, "llama-3.3-70b-versatile")
        except Exception as e:
            print(f"[AI ERROR] {e}")
            return None

    ai_history[user_id].append({
        "role": "assistant",
        "content": answer
    })
    ai_history[user_id] = ai_history[user_id][-MAX_AI_HISTORY:]

    return answer.strip() if answer else None

# === НАСТРОЙКИ ===
def load_settings():
    global settings
    data = load_json(SETTINGS_FILE, {})
    settings = data if isinstance(data, dict) else {}

def get_settings(chat_id):
    with settings_lock:
        data = settings.get(str(chat_id), {})
        s = DEFAULT_SETTINGS.copy()
        if isinstance(data, dict): s.update(data)
        return s

def save_settings_chat(chat_id, s):
    with settings_lock:
        settings[str(chat_id)] = s
        save_json(SETTINGS_FILE, settings)

# === ОБРАБОТЧИКИ ===
START_TEXT = (
    f"Привет! Я {BOT_TITLE}.\n\n"
    "Отвечаю только если:\n"
    "• Напишешь: Лакер / Лакеру / Лакера / Лакеры <текст>\n"
    "• Упомянешь меня через @{bot_username or 'бот'}\n"
    "• Ответишь на моё сообщение\n\n"
    "Обычные сообщения в чате я тихо учу.\n\n"
    "/models — выбрать модель ИИ\n"
    "/token — посмотреть/сменить ключ OpenRouter\n"
    "/good и /bad — оценить ответ реплаем"
)

@bot.message_handler(commands=["start"])
def cmd_start(message):
    bot.send_message(message.chat.id, START_TEXT, reply_to_message_id=message.message_id)

@bot.message_handler(commands=["help"])
def cmd_help(message): cmd_start(message)

@bot.message_handler(commands=["models"])
def cmd_models(message):
    s = get_settings(message.chat.id)
    current = s.get("model", "deepseek")
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(f"{'✅ ' if current == 'deepseek' else ''}DeepSeek (по умолчанию)", callback_data="model:deepseek"),
        types.InlineKeyboardButton(f"{'✅ ' if current == 'openrouter' else ''}OpenRouter (Llama 3.3 70B)", callback_data="model:openrouter"),
        types.InlineKeyboardButton(f"{'✅ ' if current == 'groq' else ''}Groq (Llama 3.3 70B)", callback_data="model:groq")
    )
    bot.send_message(message.chat.id, "🤖 Выбери модель ИИ:", reply_markup=markup)

@bot.message_handler(commands=["token"])
def cmd_token(message):
    chat_id = message.chat.id
    key_change_state[chat_id] = {"step": "password"}
    bot.send_message(chat_id, "🔐 Введи пароль для доступа к ключу OpenRouter:", reply_to_message_id=message.message_id)

@bot.message_handler(commands=["good"])
def cmd_good(message):
    rm = message.reply_to_message
    if not rm: return bot.reply_to(message, "Нужно ответить на моё сообщение.")
    if rm.from_user and bot_id is not None and rm.from_user.id != bot_id: return bot.reply_to(message, "Нужно ответить на моё сообщение.")
    text = rm.text or rm.caption or ""
    if not text: return bot.reply_to(message, "Нужно ответить на текстовое сообщение.")
    with model_lock:
        apply_positive_feedback(text, 3)
        save_if_needed(True)
    bot.reply_to(message, "Понял, запомнил как хороший ответ.")

@bot.message_handler(commands=["bad"])
def cmd_bad(message):
    rm = message.reply_to_message
    if not rm: return bot.reply_to(message, "Нужно ответить на моё сообщение.")
    if rm.from_user and bot_id is not None and rm.from_user.id != bot_id: return bot.reply_to(message, "Нужно ответить на моё сообщение.")
    text = rm.text or rm.caption or ""
    if not text: return bot.reply_to(message, "Нужно ответить на текстовое сообщение.")
    with model_lock:
        apply_negative_feedback(text, -3)
        save_if_needed(True)
    bot.reply_to(message, "Понял, постараюсь так не отвечать.")

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    try:
        if not call.data or ":" not in call.data:
            try: bot.answer_callback_query(call.id)
            except Exception: pass
            return
        action, value = call.data.split(":", 1)
        if not call.message: return
        chat_id = call.message.chat.id

        if action == "model" and value in ("deepseek", "openrouter", "groq"):
            s = get_settings(chat_id)
            s["model"] = value
            save_settings_chat(chat_id, s)
            bot.edit_message_text(f"✅ Модель изменена на: {value}", chat_id, call.message.message_id)
            try: bot.answer_callback_query(call.id, "Сохранено")
            except Exception: pass
            return

        if action == "key_change" and value == "yes":
            key_change_state[chat_id] = {"step": "waiting_new_key"}
            bot.edit_message_text(" Отправь новый API-ключ OpenRouter (начинается с sk-or-v1-...):", chat_id, call.message.message_id)
            try: bot.answer_callback_query(call.id)
            except Exception: pass
            return

        if action == "key_change" and value == "no":
            bot.edit_message_text("❌ Отмена.", chat_id, call.message.message_id)
            if chat_id in key_change_state: del key_change_state[chat_id]
            try: bot.answer_callback_query(call.id)
            except Exception: pass
            return

        try: bot.answer_callback_query(call.id)
        except Exception: pass
    except Exception: pass

@bot.message_handler(content_types=['sticker'])
def handle_sticker(message):
    if is_duplicate(message): return
    if message.from_user and bot_id is not None and message.from_user.id == bot_id: return
    
    file_id = message.sticker.file_id
    with model_lock:
        if file_id not in model.setdefault("stickers", []):
            model["stickers"].append(file_id)
            if len(model["stickers"]) > 500: model["stickers"] = model["stickers"][-500:]
            save_model_file()
            print(f"✅ Стикер сохранён: {file_id}")
            
    if message.caption:
        try:
            asyncio.run(process_message(message))
        except Exception as e:
            print(f"❌ Ошибка в handle_sticker: {e}")

async def process_message(message):
    if not message: return
    chat_id = message.chat.id
    if chat_id is None: return
    from_user = message.from_user
    if from_user and bot_id is not None and from_user.id == bot_id: return
    if from_user and from_user.is_bot: return

    text = preprocess_text(message.text or message.caption or "")
    if not text or text.startswith("/"): return

    trigger, prompt = parse_trigger(text)
    mentioned = is_bot_mentioned(message)
    force_sticker = bool(STICKER_RE.search(text))

    reply_to_bot = False
    replied_text = ""
    if message.reply_to_message:
        rm = message.reply_to_message
        if rm.from_user and bot_id is not None and rm.from_user.id == bot_id:
            reply_to_bot = True
            replied_text = rm.text or rm.caption or ""
            replied_text = preprocess_text(replied_text)

    with model_lock:
        extract_facts(chat_id, text)
        add_context(chat_id, text)
        if trigger:
            if prompt: add_phrase(prompt, is_dialog=True)
        else:
            add_phrase(text, is_dialog=False)
        if reply_to_bot and text and replied_text:
            add_pair(replied_text, text)
        maybe_rebuild_markov()
        save_if_needed()

    if not (trigger or mentioned or reply_to_bot): return

    query = prompt if (trigger and prompt) else text
    if mentioned and not trigger:
        if bot_username:
            query = query.replace("@" + bot_username, "").replace(bot_username, "").strip()
    if not query and reply_to_bot: query = replied_text
    if not query: query = text

    s = get_settings(chat_id)
    selected_model = s.get("model", "deepseek")
    
    try:
        bot.send_chat_action(chat_id, "typing")
        user_name = message.from_user.first_name or "Пользователь"
        
        answer = await ask_ai(chat_id, user_name, query, selected_model)
        
        if not answer:
            answer = "Братан я щас в туалете мне лень отвечать"
        
        thread_id = getattr(message, "message_thread_id", None)
        send_answer_and_sticker(chat_id, answer, reply_message_id=message.message_id, thread_id=thread_id, force_sticker=force_sticker)
        return
    except Exception as e:
        print(f"❌ Ошибка ИИ: {e}")
        traceback.print_exc()
        answer = "Братан я щас в туалете мне лень отвечать"
        thread_id = getattr(message, "message_thread_id", None)
        send_answer_and_sticker(chat_id, answer, reply_message_id=message.message_id, thread_id=thread_id, force_sticker=force_sticker)

def mask_key(key):
    """Маскирует ключ: показывает первые 15 и последние 4 символа"""
    if not key or len(key) < 20:
        return "***скрыт***"
    return f"{key[:15]}...{key[-4:]}"

@bot.message_handler(content_types=["text"], func=lambda m: m.text and not m.text.strip().startswith("/"))
def text_handler(message):
    if is_duplicate(message): return
    
    # Обработка смены ключа OpenRouter
    chat_id = message.chat.id
    if chat_id in key_change_state:
        state = key_change_state[chat_id]
        
        if state["step"] == "password":
            if message.text.strip() == KEY_PASSWORD:
                state["step"] = "show_key"
                masked = mask_key(OPENROUTER_API_KEY)
                markup = types.InlineKeyboardMarkup(row_width=2)
                markup.add(
                    types.InlineKeyboardButton("🔄 Сменить ключ", callback_data="key_change:yes"),
                    types.InlineKeyboardButton("❌ Отмена", callback_data="key_change:no")
                )
                bot.send_message(
                    chat_id, 
                    f"🔑 Текущий ключ OpenRouter:\n\n<code>{masked}</code>\n\nНажми кнопку чтобы сменить:", 
                    reply_markup=markup, 
                    parse_mode="HTML"
                )
                del key_change_state[chat_id]
            else:
                bot.send_message(chat_id, "❌ Неверный пароль.")
                del key_change_state[chat_id]
            return
        
        elif state["step"] == "waiting_new_key":
            new_key = message.text.strip()
            if new_key and (new_key.startswith("sk-or-v1-") or len(new_key) > 30):
                global OPENROUTER_API_KEY
                OPENROUTER_API_KEY = new_key
                # Сохраняем в файл
                try:
                    with open(OPENROUTER_KEY_FILE, "w") as f:
                        f.write(new_key)
                    bot.send_message(chat_id, f"✅ Ключ OpenRouter обновлён и сохранён!\nНовый ключ: {mask_key(new_key)}")
                    print(f"✅ Ключ OpenRouter обновлён: {mask_key(new_key)}")
                except Exception as e:
                    bot.send_message(chat_id, f"❌ Ошибка сохранения: {e}")
            else:
                bot.send_message(chat_id, "❌ Неверный формат ключа. Ключ должен начинаться с sk-or-v1-")
            del key_change_state[chat_id]
            return
    
    try:
        asyncio.run(process_message(message))
    except Exception as e:
        print(f"❌ Ошибка в text_handler: {e}")
        traceback.print_exc()

def process_channel_post(message):
    try:
        text = message.text or message.caption or ""
        text = preprocess_text(text)
        if not text: return
        chat_id = message.chat.id
        trigger, prompt = parse_trigger(text)
        s = get_settings(chat_id)
        mode = s.get("channel", "all")
        if mode == "off": return
        with model_lock:
            extract_facts(chat_id, text)
            add_context(chat_id, text)
            add_phrase(text, is_dialog=False)
            save_if_needed()
        if mode == "trigger" and not trigger: return
        query = prompt if trigger and prompt else text
        
        selected_model = s.get("model", "deepseek")
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            answer = loop.run_until_complete(ask_ai(chat_id, "Канал", query, selected_model))
            loop.close()
            if not answer: answer = "Братан я щас в туалете мне лень отвечать"
        except Exception:
            answer = "Братан я щас в туалете мне лень отвечать"
        
        send_answer_and_sticker(chat_id, answer, reply_message_id=message.message_id)
    except Exception: pass

def on_channel_post(post): process_channel_post(post)

if hasattr(bot, "channel_post_handler"):
    try: bot.channel_post_handler(func=lambda post: True)(on_channel_post)
    except Exception: pass

def store_bot_message(sent_message, answer, chat_id):
    if not sent_message: return
    message_id = getattr(sent_message, "message_id", None)
    if message_id is None: return
    key = f"{chat_id}:{message_id}"
    with model_lock:
        bm = model.setdefault("bot_messages", {})
        bm[key] = {"text": answer, "chat_id": chat_id, "ts": time.time()}
        if len(bm) > 1000:
            items = sorted(bm.items(), key=lambda x: x[1].get("ts", 0))
            for k, _ in items[:len(bm) - 1000]: del bm[k]
        add_recent_answer(chat_id, answer)
        save_if_needed(True)

def send_answer_and_sticker(chat_id, answer, reply_message_id=None, thread_id=None, force_sticker=False):
    sent_msg = None
    try:
        if thread_id:
            try: sent_msg = bot.send_message(chat_id, answer, reply_to_message_id=reply_message_id, message_thread_id=thread_id)
            except TypeError: sent_msg = bot.send_message(chat_id, answer, reply_to_message_id=reply_message_id)
        else:
            sent_msg = bot.send_message(chat_id, answer, reply_to_message_id=reply_message_id)
        store_bot_message(sent_msg, answer, chat_id)
    except Exception: pass

    stickers = model.get("stickers", [])
    if stickers:
        should_send_sticker = force_sticker or (random.random() < STICKER_CHANCE)
        if should_send_sticker:
            try:
                sticker_id = random.choice(stickers)
                if thread_id:
                    bot.send_sticker(chat_id, sticker_id, reply_to_message_id=reply_message_id, message_thread_id=thread_id)
                else:
                    bot.send_sticker(chat_id, sticker_id, reply_to_message_id=reply_message_id)
            except Exception: pass

def main():
    global bot_id, bot_username, settings, faiss_index, knowledge_graph, tfidf_vectorizer, tfidf_matrix, OPENROUTER_API_KEY
    
    # Загружаем ключ OpenRouter из файла при старте
    if os.path.exists(OPENROUTER_KEY_FILE):
        try:
            with open(OPENROUTER_KEY_FILE, "r") as f:
                saved_key = f.read().strip()
            if saved_key and len(saved_key) > 10:
                OPENROUTER_API_KEY = saved_key
                print(f"✅ Загружен ключ OpenRouter из {OPENROUTER_KEY_FILE}")
        except Exception: pass
    
    load_model()
    load_settings()
    rebuild_markov(force=True)
    model.setdefault("facts", {})
    model.setdefault("recent_context", {})
    model.setdefault("stickers", [])
    
    rebuild_faiss_index()
    build_knowledge_graph()
    build_tfidf_clusters()
    
    for attempt in range(5):
        try:
            me = bot.get_me()
            bot_id = me.id
            bot_username = me.username
            break
        except Exception:
            time.sleep(3)
    if bot_id is None: raise SystemExit("Не удалось получить bot_id через getMe.")
    
    print(f"{BOT_TITLE} запущен (AI режим).")
    print(f"bot_id={bot_id}, username=@{bot_username}")
    print(f"Ключ OpenRouter: {mask_key(OPENROUTER_API_KEY)}")
    print(f"Стикеров в памяти: {len(model.get('stickers', []))}")
    atexit.register(save_all)
    
    try:
        bot.infinity_polling(skip_pending=True, allowed_updates=["message", "callback_query", "channel_post", "message_reaction"])
    except KeyboardInterrupt: 
        pass
    except Exception as e:
        print(f"Ошибка polling: {e}")
    finally: 
        save_all()

if __name__ == "__main__":
    main()
