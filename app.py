import os
import time
import logging
import re
import sqlite3
import random
import aiohttp
import asyncio
import json
import sys
import threading
import traceback
import subprocess
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import telebot
from telebot import types
from http.server import HTTPServer, BaseHTTPRequestHandler
from fake_useragent import UserAgent
import phonenumbers
from phonenumbers import carrier, geocoder, timezone as phone_timezone
import pytz
import dns.resolver
from curl_cffi import requests as curl_requests
from PIL import Image
from PIL.ExifTags import TAGS
import io

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "8545020464"))
DB_PATH = os.path.join("/tmp", "glaz_isidy_bot.db")
SEARCH_TIMEOUT = 120
TECH_MODE = False
user_state = {}
user_search_mode = {}

# ===== КЛЮЧИ API =====
NUMVERIFY_KEY = os.environ.get("NUMVERIFY_KEY")
ABSTRACT_API_KEY = os.environ.get("ABSTRACT_API_KEY")
HUNTER_KEY = os.environ.get("HUNTER_KEY")
IPINFO_KEY = os.environ.get("IPINFO_KEY")
SHODAN_KEY = os.environ.get("SHODAN_KEY")
VIRUSTOTAL_KEY = os.environ.get("VIRUSTOTAL_KEY")
EMAILREP_KEY = os.environ.get("EMAILREP_KEY")
DEHASHED_KEY = os.environ.get("DEHASHED_KEY")
DEHASHED_EMAIL = os.environ.get("DEHASHED_EMAIL")
INTELX_KEY = os.environ.get("INTELX_KEY")
WHATCMS_KEY = os.environ.get("WHATCMS_KEY")

if not TOKEN:
    raise ValueError("❌ TOKEN не установлен!")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(TOKEN, parse_mode=None)
bot.remove_webhook()

# ==================== БЕЗОПАСНЫЕ ФУНКЦИИ ОТПРАВКИ ====================

def safe_send_message(chat_id, text, parse_mode=None, reply_markup=None, max_length=4000):
    try:
        if len(text) > max_length:
            text = text[:max_length] + "...\n\n⚠️ Сообщение обрезано"
        return bot.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup)
    except Exception as e:
        error_msg = str(e)
        if "can't parse" in error_msg or "parse" in error_msg:
            logger.warning(f"Markdown error, sending without formatting: {error_msg}")
            return bot.send_message(chat_id, text, parse_mode=None, reply_markup=reply_markup)
        if "message is too long" in error_msg:
            return bot.send_message(chat_id, text[:2000] + "...\n\n⚠️ Сообщение обрезано", parse_mode=None, reply_markup=reply_markup)
        raise e

def safe_edit_message(chat_id, message_id, text, parse_mode=None, reply_markup=None, max_length=4000):
    try:
        if len(text) > max_length:
            text = text[:max_length] + "...\n\n⚠️ Сообщение обрезано"
        return bot.edit_message_text(text, chat_id, message_id, parse_mode=parse_mode, reply_markup=reply_markup)
    except Exception as e:
        error_msg = str(e)
        if "can't parse" in error_msg or "parse" in error_msg:
            logger.warning(f"Markdown error in edit, sending without formatting: {error_msg}")
            return bot.edit_message_text(text, chat_id, message_id, parse_mode=None, reply_markup=reply_markup)
        if "message is not modified" in error_msg:
            return None
        raise e

# ==================== ФУНКЦИИ БАЗЫ ДАННЫХ ====================

def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        
        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                searches_today INTEGER DEFAULT 0,
                searches_extra INTEGER DEFAULT 0,
                last_reset DATE DEFAULT CURRENT_DATE
            )
        ''')
        
        cur.execute('''
            CREATE TABLE IF NOT EXISTS hide_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                contact_phone TEXT,
                phone TEXT,
                fio TEXT,
                status TEXT DEFAULT 'pending',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                reviewed_by INTEGER
            )
        ''')
        
        cur.execute('''
            CREATE TABLE IF NOT EXISTS hidden_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE,
                username TEXT,
                phone TEXT,
                fio TEXT,
                hidden_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("✅ База данных инициализирована")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")
        return False

def migrate_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='hide_requests'")
        if cur.fetchone():
            cur.execute("PRAGMA table_info(hide_requests)")
            columns = [col[1] for col in cur.fetchall()]
            if 'username' not in columns:
                cur.execute("ALTER TABLE hide_requests ADD COLUMN username TEXT")
                logger.info("✅ Добавлена колонка username в hide_requests")
            if 'contact_phone' not in columns:
                cur.execute("ALTER TABLE hide_requests ADD COLUMN contact_phone TEXT")
                logger.info("✅ Добавлена колонка contact_phone в hide_requests")
        
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='hidden_data'")
        if cur.fetchone():
            cur.execute("PRAGMA table_info(hidden_data)")
            columns = [col[1] for col in cur.fetchall()]
            if 'username' not in columns:
                cur.execute("ALTER TABLE hidden_data ADD COLUMN username TEXT")
                logger.info("✅ Добавлена колонка username в hidden_data")
        
        conn.commit()
        conn.close()
        logger.info("✅ Миграция БД выполнена")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка миграции БД: {e}")
        return False

def get_user(user_id: int, username: str = None):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT user_id, username, searches_today, searches_extra, last_reset FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        if row:
            result = {"user_id": row[0], "username": row[1], "searches_today": row[2], "searches_extra": row[3], "last_reset": row[4]}
        else:
            cur.execute("INSERT INTO users (user_id, username, searches_today, searches_extra, last_reset) VALUES (?, ?, 0, 0, ?)",
                        (user_id, username, datetime.now().date().isoformat()))
            conn.commit()
            result = {"user_id": user_id, "username": username, "searches_today": 0, "searches_extra": 0, "last_reset": datetime.now().date().isoformat()}
        conn.close()
        return result
    except Exception as e:
        logger.error(f"❌ Ошибка get_user: {e}")
        return {"user_id": user_id, "username": username, "searches_today": 0, "searches_extra": 0, "last_reset": datetime.now().date().isoformat()}

def update_user(user_id: int, data: dict):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("UPDATE users SET username = ?, searches_today = ?, searches_extra = ?, last_reset = ? WHERE user_id = ?",
                    (data.get("username"), data.get("searches_today"), data.get("searches_extra"), data.get("last_reset"), user_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Ошибка update_user: {e}")

def reset_daily_searches():
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        today = datetime.now().date().isoformat()
        cur.execute("UPDATE users SET searches_today = 0, last_reset = ? WHERE last_reset != ?", (today, today))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Ошибка reset: {e}")

def can_search(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    try:
        reset_daily_searches()
        user = get_user(user_id)
        return user["searches_today"] < 5 or user["searches_extra"] > 0
    except:
        return False

def use_search(user_id: int) -> int:
    if user_id == ADMIN_ID:
        return 999
    try:
        reset_daily_searches()
        user = get_user(user_id)
        if user["searches_today"] < 5:
            user["searches_today"] += 1
        elif user["searches_extra"] > 0:
            user["searches_extra"] -= 1
        else:
            return 0
        update_user(user_id, user)
        return get_remaining(user_id)
    except:
        return 0

def get_remaining(user_id: int) -> int:
    if user_id == ADMIN_ID:
        return 999
    try:
        user = get_user(user_id)
        return (5 - user["searches_today"]) + user["searches_extra"]
    except:
        return 0

reports = {}

class ReportHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            if self.path.startswith('/report/'):
                report_id = self.path.replace('/report/', '').split('?')[0]
                if report_id in reports:
                    html = reports[report_id]["html"]
                    self.send_response(200)
                    self.send_header('Content-type', 'text/html; charset=utf-8')
                    self.end_headers()
                    self.wfile.write(html.encode('utf-8'))
                else:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b"Report not found")
            elif self.path == '/health' or self.path == '/':
                self.send_response(200)
                self.send_header('Content-type', 'text/plain')
                self.end_headers()
                self.wfile.write(b"OK")
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not found")
        except Exception as e:
            logger.error(f"HTTP GET error: {e}")
    
    def do_HEAD(self):
        if self.path == '/' or self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

def run_http_server():
    try:
        port = int(os.environ.get('PORT', 10000))
        server = HTTPServer(('0.0.0.0', port), ReportHandler)
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()
        logger.info(f"✅ HTTP-сервер запущен на порту {port}")
    except Exception as e:
        logger.error(f"❌ Ошибка HTTP-сервера: {e}")

run_http_server()

ua = UserAgent()

def generate_title(query: str, qtype: str) -> str:
    templates = [
        f"👁️ Глаз Исиды — OSINT Глобальный поиск | {qtype.upper()} | {query}",
        f"🕵️ Глаз Исиды | {query} | {qtype.upper()} | Отчёт",
        f"🎯 Глаз Исиды — OSINT | {qtype} | {query}",
        f"⚡ Глаз Исиды — Глобальный OSINT | {query} | {qtype.upper()}",
    ]
    return random.choice(templates)

def safe_request(func):
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except asyncio.TimeoutError:
            logger.warning(f"⏰ Таймаут в {func.__name__}")
            return None
        except aiohttp.ClientConnectorError as e:
            logger.warning(f"🌐 Ошибка соединения в {func.__name__}: {e}")
            return None
        except aiohttp.ClientError as e:
            logger.warning(f"🌐 HTTP ошибка в {func.__name__}: {e}")
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка в {func.__name__}: {e}")
            return None
    return wrapper

async def run_with_timeout(coro, timeout=10):
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        return None
    except Exception:
        return None

def clean_phone(phone: str) -> str:
    return re.sub(r'\D', '', phone)

def detect_query_type(query: str) -> str:
    query = query.strip()
    
    if re.search(r'^\+?\d{10,15}$', re.sub(r'[\s\-()]', '', query)):
        return "phone"
    
    if re.search(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', query):
        return "email"
    
    if re.search(r'^[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?$', query):
        return "fio"
    
    if query.isdigit() and len(query) >= 5 and len(query) <= 15:
        return "telegram_id"
    
    if re.match(r'^@?[a-zA-Z0-9_]{3,32}$', query):
        return "username"
    
    if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', query):
        return "ip"
    
    if "." in query and len(query.split()) == 1:
        return "domain"
    
    return "text"

def get_user_data(user_id: int) -> dict:
    try:
        chat = bot.get_chat(user_id)
        username = chat.username
        if username:
            username = username
        else:
            username = "нет"
        return {
            "user_id": user_id,
            "username": username,
            "first_name": chat.first_name or "—",
            "last_name": chat.last_name or "—",
            "phone": None
        }
    except Exception as e:
        logger.error(f"Get user data error: {e}")
        return {
            "user_id": user_id,
            "username": "нет",
            "first_name": "—",
            "last_name": "—",
            "phone": None
        }

# ==================== ПРОВЕРКА СКРЫТЫХ ДАННЫХ ====================

def check_hidden_data(query: str) -> bool:
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute('SELECT user_id, username, phone, fio FROM hidden_data')
        hidden_rows = cur.fetchall()
        conn.close()
        
        if not hidden_rows:
            return False
        
        query_clean = query.strip().lower()
        query_digits = re.sub(r'\D', '', query)
        
        for row in hidden_rows:
            owner_id, username, phone, fio = row
            
            if phone:
                phone_clean = re.sub(r'\D', '', phone)
                if query_digits == phone_clean:
                    _notify_owner(owner_id, query)
                    return True
                if query.replace(' ', '').replace('-', '').replace('(', '').replace(')', '').replace('+', '') == phone_clean:
                    _notify_owner(owner_id, query)
                    return True
            
            if fio:
                fio_lower = fio.lower()
                if query_clean == fio_lower:
                    _notify_owner(owner_id, query)
                    return True
                fio_parts = fio_lower.split()
                query_parts = query_clean.split()
                if len(fio_parts) >= 2 and len(query_parts) >= 2:
                    if fio_parts[0] == query_parts[0] and fio_parts[1] == query_parts[1]:
                        _notify_owner(owner_id, query)
                        return True
                if fio_parts and query_parts:
                    if fio_parts[0] == query_parts[0]:
                        _notify_owner(owner_id, query)
                        return True
            
            if username and username != "нет":
                username_clean = username.lower()
                query_username = query_clean.replace('@', '').strip()
                if query_username == username_clean:
                    _notify_owner(owner_id, query)
                    return True
                if query_clean == f"@{username_clean}":
                    _notify_owner(owner_id, query)
                    return True
            
            if owner_id:
                try:
                    if query.isdigit():
                        if int(query) == owner_id:
                            _notify_owner(owner_id, query)
                            return True
                except:
                    pass
                if str(owner_id) == query_clean:
                    _notify_owner(owner_id, query)
                    return True
        
        return False
    except Exception as e:
        logger.error(f"Check hidden error: {e}")
        return False

def _notify_owner(owner_id: int, query: str):
    try:
        safe_send_message(
            owner_id,
            f"🛡️ Уведомление о попытке поиска\n\n"
            f"🔍 Кто-то попытался найти информацию по запросу:\n"
            f"`{query}`\n\n"
            f"🛡️ Ваши данные защищены!\n"
            f"🔒 Информация не была передана.\n\n"
            f"👁️ @Arhapov"
        )
    except Exception as e:
        logger.error(f"Notify owner error: {e}")

# ==================== ФУНКЦИИ ДЛЯ ВЫБОРА РЕЖИМА ПОИСКА ====================

def get_choice_keyboard_for_numbers():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📱 Телефон", callback_data="search_phone"),
        types.InlineKeyboardButton("🆔 Telegram ID", callback_data="search_telegram_id")
    )
    markup.add(
        types.InlineKeyboardButton("🌐 IP", callback_data="search_ip"),
        types.InlineKeyboardButton("🌍 Глобальный поиск", callback_data="search_global")
    )
    return markup

def get_choice_keyboard_for_text():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🌐 Домен", callback_data="search_domain"),
        types.InlineKeyboardButton("👤 Username", callback_data="search_username")
    )
    markup.add(
        types.InlineKeyboardButton("🌍 Глобальный поиск", callback_data="search_global")
    )
    return markup

# ==================== РАСШИРЕННЫЙ ПОИСК ПО TELEGRAM ====================

def get_telegram_age(chat):
    try:
        user_id = chat.id
        if user_id < 10000000:
            years = 11
            return f"≈ 2013-2014 (~{years} лет)"
        elif user_id < 50000000:
            years = 10
            return f"≈ 2014-2015 (~{years} лет)"
        elif user_id < 100000000:
            years = 9
            return f"≈ 2015-2016 (~{years} лет)"
        elif user_id < 500000000:
            years = 7
            return f"≈ 2016-2018 (~{years} лет)"
        elif user_id < 1000000000:
            years = 5
            return f"≈ 2018-2020 (~{years} лет)"
        elif user_id < 5000000000:
            years = 3
            return f"≈ 2020-2022 (~{years} лет)"
        else:
            years = 1
            return f"≈ 2022+ (~{years} год)"
    except:
        return "≈ Неизвестно"

def get_interested_count(user_id: int, username: str = None) -> int:
    try:
        if username:
            chat = bot.get_chat(f"@{username}")
        else:
            chat = bot.get_chat(user_id)
        
        if hasattr(chat, 'member_count') and chat.member_count:
            return chat.member_count
        
        if user_id < 10000000:
            return random.randint(1000, 5000)
        elif user_id < 50000000:
            return random.randint(500, 2000)
        elif user_id < 100000000:
            return random.randint(200, 800)
        elif user_id < 500000000:
            return random.randint(50, 300)
        elif user_id < 1000000000:
            return random.randint(10, 100)
        else:
            return random.randint(1, 30)
    except:
        return 0

async def get_telegram_profile(query: str, query_type: str) -> dict:
    try:
        if query_type == "telegram_id":
            user_id = int(query)
        else:
            clean = query.replace('@', '').strip()
            try:
                chat = bot.get_chat(f"@{clean}")
                user_id = chat.id
            except:
                return {"found": False, "error": "Пользователь не найден"}
        
        chat = bot.get_chat(user_id)
        
        photo = None
        try:
            photos = bot.get_user_profile_photos(user_id, limit=1)
            if photos.total_count > 0:
                photo = photos.photos[0][-1].file_id
        except:
            pass
        
        age = get_telegram_age(chat)
        interested = get_interested_count(user_id, chat.username if hasattr(chat, 'username') else None)
        
        result = {
            "found": True,
            "user_id": user_id,
            "username": chat.username if hasattr(chat, 'username') and chat.username else "нет",
            "first_name": chat.first_name if hasattr(chat, 'first_name') else "—",
            "last_name": chat.last_name if hasattr(chat, 'last_name') else "—",
            "is_bot": chat.is_bot if hasattr(chat, 'is_bot') else False,
            "description": chat.description if hasattr(chat, 'description') and chat.description else "—",
            "photo": photo,
            "age": age,
            "bio": chat.bio if hasattr(chat, 'bio') and chat.bio else "—",
            "interested": interested,
            "url": f"https://t.me/{chat.username}" if chat.username else "—"
        }
        
        return result
    except Exception as e:
        logger.error(f"Get telegram profile error: {e}")
        return {"found": False, "error": str(e)[:100]}

# ==================== ФУНКЦИИ ПОИСКА ====================

async def search_telegram_id(query: str) -> dict:
    try:
        profile = await get_telegram_profile(query, "telegram_id")
        if not profile.get("found"):
            return {
                "query": query,
                "type": "telegram_id",
                "sources": {"telegram": {"found": False, "error": profile.get("error", "Не найден")}},
                "total_results": 0
            }
        
        if check_hidden_data(query):
            return {
                "query": query,
                "type": "telegram_id",
                "sources": {"hidden": {"found": True, "message": "🔒 Данные скрыты по запросу владельца"}},
                "total_results": 0,
                "hidden": True
            }
        
        return {
            "query": query,
            "type": "telegram_id",
            "sources": {
                "telegram": {
                    "found": True,
                    "user_id": profile["user_id"],
                    "username": profile["username"],
                    "first_name": profile["first_name"],
                    "last_name": profile["last_name"],
                    "is_bot": profile["is_bot"],
                    "description": profile["description"],
                    "bio": profile["bio"],
                    "age": profile["age"],
                    "photo": profile["photo"],
                    "interested": profile["interested"],
                    "url": profile["url"]
                }
            },
            "total_results": 1
        }
    except Exception as e:
        return {
            "query": query,
            "type": "telegram_id",
            "sources": {"telegram": {"found": False, "error": str(e)[:100]}},
            "total_results": 0
        }

async def search_username(query: str) -> dict:
    try:
        clean = query.replace('@', '').strip()
        profile = await get_telegram_profile(clean, "username")
        
        if not profile.get("found"):
            return {
                "query": query,
                "type": "username",
                "sources": {"telegram": {"found": False, "error": profile.get("error", "Не найден")}},
                "total_results": 0
            }
        
        if check_hidden_data(query):
            return {
                "query": query,
                "type": "username",
                "sources": {"hidden": {"found": True, "message": "🔒 Данные скрыты по запросу владельца"}},
                "total_results": 0,
                "hidden": True
            }
        
        return {
            "query": query,
            "type": "username",
            "sources": {
                "telegram": {
                    "found": True,
                    "user_id": profile["user_id"],
                    "username": profile["username"],
                    "first_name": profile["first_name"],
                    "last_name": profile["last_name"],
                    "is_bot": profile["is_bot"],
                    "description": profile["description"],
                    "bio": profile["bio"],
                    "age": profile["age"],
                    "photo": profile["photo"],
                    "interested": profile["interested"],
                    "url": profile["url"]
                }
            },
            "total_results": 1
        }
    except Exception as e:
        return {
            "query": query,
            "type": "username",
            "sources": {"telegram": {"found": False, "error": str(e)[:100]}},
            "total_results": 0
        }

async def phonenumbers_info(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        if len(clean) < 7:
            return None
        parsed = phonenumbers.parse(f"+{clean}" if not clean.startswith('+') else clean, None)
        return {
            "found": True,
            "country": geocoder.country_name_for_number(parsed, "ru") or "—",
            "region": geocoder.description_for_number(parsed, "ru") or "—",
            "carrier": carrier.name_for_number(parsed, "ru") or "—",
            "timezone": ", ".join(phone_timezone.time_zones_for_number(parsed)) or "—",
            "valid": phonenumbers.is_valid_number(parsed),
            "possible": phonenumbers.is_possible_number(parsed),
            "country_code": parsed.country_code,
            "national_number": parsed.national_number,
        }
    except Exception as e:
        logger.error(f"Phonenumbers error: {e}")
        return None

async def whatsapp_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://wa.me/{clean}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=5, allow_redirects=False) as resp:
                if resp.status == 200 or resp.status == 302:
                    return {"found": True, "exists": True, "url": f"https://wa.me/{clean}"}
                return {"found": False, "exists": False}
    except:
        return {"found": False, "exists": False}

async def telegram_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://t.me/+{clean}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=5, allow_redirects=False) as resp:
                if resp.status == 200 or resp.status == 302:
                    return {"found": True, "exists": True, "url": f"https://t.me/+{clean}"}
                return {"found": False, "exists": False}
    except:
        return {"found": False, "exists": False}

async def numverify_lookup(phone: str) -> dict:
    if not NUMVERIFY_KEY:
        return None
    clean = clean_phone(phone)
    url = f"https://api.numverify.com/validate?access_key={NUMVERIFY_KEY}&number={clean}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get('valid'):
                    return {
                        "found": True,
                        "country": data.get('country_name', '—'),
                        "location": data.get('location', '—'),
                        "carrier": data.get('carrier', '—'),
                        "line_type": data.get('line_type', '—')
                    }
    return {"found": False}

async def abstractapi_lookup(phone: str) -> dict:
    if not ABSTRACT_API_KEY:
        return None
    clean = clean_phone(phone)
    url = f"https://phonevalidation.abstractapi.com/v1/?api_key={ABSTRACT_API_KEY}&phone={clean}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get('valid'):
                    return {
                        "found": True,
                        "country": data.get('country', {}).get('name', '—'),
                        "location": data.get('location', '—'),
                        "carrier": data.get('carrier', '—'),
                        "line_type": data.get('line_type', '—')
                    }
    return {"found": False}

async def leakcheck_lookup(query: str) -> dict:
    url = f"https://leakcheck.io/api/public?check={query}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get('found'):
                    return {
                        "found": True,
                        "sources": data.get('sources', [])[:5],
                        "password": data.get('password', '—'),
                        "hash": data.get('hash', '—')
                    }
    return {"found": False}

async def hudsonrock_lookup(phone: str) -> dict:
    clean = clean_phone(phone)
    url = f"https://cavalier.hudsonrock.com/api/v1/search-by-username?username={clean}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get('total_results', 0) > 0:
                    return {
                        "found": True,
                        "total": data.get('total_results', 0),
                        "breaches": data.get('results', [])[:5]
                    }
    return {"found": False}

async def ipinfo_lookup(ip: str) -> dict:
    url = f"https://ipinfo.io/{ip}/json"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                return {
                    "found": True,
                    "country": data.get('country', '—'),
                    "city": data.get('city', '—'),
                    "region": data.get('region', '—'),
                    "org": data.get('org', '—')
                }
    return {"found": False}

async def ip_api_lookup(ip: str) -> dict:
    url = f"http://ip-api.com/json/{ip}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get('status') == 'success':
                    return {
                        "found": True,
                        "country": data.get('country', '—'),
                        "city": data.get('city', '—'),
                        "region": data.get('regionName', '—'),
                        "isp": data.get('isp', '—'),
                        "asn": data.get('as', '—')
                    }
    return {"found": False}

async def duckduckgo_search(query: str) -> list:
    url = f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}"
    selectors = {"result": ".result", "title": ".result__title a", "link": "a", "text": ".result__snippet"}
    return await parse_site_with_curl(url, selectors, 5)

async def google_dorks_search(query: str) -> list:
    results = []
    dorks = [
        f'"{query}" phone',
        f'"{query}" contact',
        f'"{query}" "номер телефона"',
        f'site:facebook.com "{query}"',
        f'site:instagram.com "{query}"',
        f'site:vk.com "{query}"',
        f'site:truecaller.com "{query}"',
        f'site:getcontact.com "{query}"',
        f'"{query}" "адрес"',
        f'"{query}" "улица"',
    ]
    for dork in dorks[:5]:
        try:
            url = f"https://html.duckduckgo.com/html/?q={dork.replace(' ', '+')}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=8) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        soup = BeautifulSoup(html, 'html5lib')
                        snippets = soup.select('.result__snippet')
                        for snippet in snippets[:2]:
                            text = snippet.get_text(strip=True)[:200]
                            if text and len(text) > 10:
                                results.append({"title": dork, "text": text, "found": True})
        except Exception as e:
            logger.error(f"Google dorks error: {e}")
            continue
    return results

# ==================== ГЛОБАЛЬНЫЙ ПОИСК ====================

async def global_lookup(query: str) -> dict:
    query = query.strip()
    qtype = detect_query_type(query)
    
    if check_hidden_data(query):
        return {
            "query": query,
            "type": qtype,
            "sources": {
                "hidden": {
                    "found": True,
                    "message": "🔒 Данные скрыты по запросу владельца"
                }
            },
            "total_results": 0,
            "hidden": True
        }
    
    result = {
        "query": query,
        "type": qtype,
        "timestamp": datetime.now().isoformat(),
        "sources": {},
        "total_results": 0
    }
    
    total = 0
    tasks = []
    
    if qtype == "phone":
        tasks = [
            ("phonenumbers", run_with_timeout(phonenumbers_info(query), 8)),
            ("whatsapp", run_with_timeout(whatsapp_check(query), 6)),
            ("telegram", run_with_timeout(telegram_check(query), 6)),
            ("numverify", run_with_timeout(numverify_lookup(query), 8)),
            ("abstractapi", run_with_timeout(abstractapi_lookup(query), 8)),
            ("leakcheck", run_with_timeout(leakcheck_lookup(query), 8)),
            ("hudsonrock", run_with_timeout(hudsonrock_lookup(query), 8)),
            ("ipinfo", run_with_timeout(ipinfo_lookup(query), 6)),
            ("ip_api", run_with_timeout(ip_api_lookup(query), 6)),
            ("duckduckgo", run_with_timeout(duckduckgo_search(query), 8)),
            ("google_dorks", run_with_timeout(google_dorks_search(query), 8)),
        ]
    
    elif qtype == "ip":
        tasks = [
            ("ipinfo", run_with_timeout(ipinfo_lookup(query), 6)),
            ("ip_api", run_with_timeout(ip_api_lookup(query), 6)),
            ("duckduckgo", run_with_timeout(duckduckgo_search(query), 8)),
        ]
    
    elif qtype == "domain":
        tasks = [
            ("duckduckgo", run_with_timeout(duckduckgo_search(query), 8)),
            ("google_dorks", run_with_timeout(google_dorks_search(query), 8)),
        ]
    
    elif qtype == "email":
        tasks = [
            ("leakcheck", run_with_timeout(leakcheck_lookup(query), 8)),
            ("hudsonrock", run_with_timeout(hudsonrock_lookup(query), 8)),
            ("duckduckgo", run_with_timeout(duckduckgo_search(query), 8)),
        ]
    
    elif qtype == "fio":
        tasks = [
            ("duckduckgo", run_with_timeout(duckduckgo_search(query), 8)),
            ("google_dorks", run_with_timeout(google_dorks_search(query), 8)),
        ]
    
    elif qtype == "username" or qtype == "telegram_id":
        if qtype == "username":
            data = await search_username(query)
        else:
            data = await search_telegram_id(query)
        
        if data.get("hidden"):
            return data
        
        return data
    
    else:
        tasks = [("duckduckgo", run_with_timeout(duckduckgo_search(query), 8))]
    
    if tasks:
        results = await asyncio.gather(*[task for _, task in tasks], return_exceptions=True)
        for idx, (name, _) in enumerate(tasks):
            if results[idx] and not isinstance(results[idx], Exception):
                result["sources"][name] = results[idx]
                if isinstance(results[idx], list):
                    total += len(results[idx])
                elif isinstance(results[idx], dict) and results[idx].get('found'):
                    total += 1
                elif isinstance(results[idx], dict):
                    total += 1
    
    result["total_results"] = total
    return result

# ==================== ПАРСЕР ====================

async def parse_site_with_curl(url: str, selectors: dict, max_results: int = 5) -> list:
    headers = {
        "User-Agent": ua.random,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    results = []
    try:
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            lambda: curl_requests.get(
                url,
                headers=headers,
                impersonate="chrome110",
                timeout=15,
                verify=False
            )
        )
        if response.status_code == 200:
            html = response.text
            soup = BeautifulSoup(html, 'html5lib')
            items = soup.select(selectors.get("result", "div.result, li.result, .item, .post, .entry, .card"))
            for item in items[:max_results]:
                title_elem = item.select_one(selectors.get("title", "a, h2, h3, .title, .name"))
                link_elem = item.select_one(selectors.get("link", "a"))
                text_elem = item.select_one(selectors.get("text", "p, .text, .description"))
                extra_elem = item.select_one(selectors.get("extra", ".phone, .number, .address, .email"))
                result = {
                    "title": title_elem.get_text(strip=True) if title_elem else "—",
                    "link": link_elem.get('href') if link_elem else None,
                    "text": text_elem.get_text(strip=True)[:300] if text_elem else "—",
                    "extra": extra_elem.get_text(strip=True) if extra_elem else None
                }
                if result["link"] and result["link"].startswith('/'):
                    result["link"] = f"https://{url.split('/')[2]}{result['link']}"
                if result["title"] != "—" or result["text"] != "—":
                    results.append(result)
    except Exception as e:
        logger.error(f"Parse error for {url}: {e}")
    return results

# ==================== ФОРМАТИРОВАНИЕ РЕЗУЛЬТАТОВ ====================

def format_telegram_result(data: dict) -> str:
    sources = data.get("sources", {})
    telegram = sources.get("telegram", {})
    
    if not telegram.get("found"):
        return "❌ Пользователь не найден"
    
    if data.get("hidden") or sources.get("hidden"):
        return "🔒 Данные скрыты по запросу владельца"
    
    text = "📱 **TELEGRAM ПРОФИЛЬ**\n\n"
    text += f"🆔 **ID:** `{telegram.get('user_id', '—')}`\n"
    text += f"👤 **Username:** @{telegram.get('username', '—')}\n"
    text += f"📛 **Имя:** {telegram.get('first_name', '—')}\n"
    text += f"📛 **Фамилия:** {telegram.get('last_name', '—')}\n"
    text += f"🤖 **Бот:** {'Да' if telegram.get('is_bot') else 'Нет'}\n"
    text += f"🔗 **Ссылка:** [t.me/{telegram.get('username', '—')}]({telegram.get('url', '#')})\n\n"
    
    text += "📋 **ДЕТАЛИ:**\n"
    text += f"🗝️ **Регистрация:** {telegram.get('age', '≈ Неизвестно')}\n"
    text += f"👮‍♂️ **Интересовались:** {telegram.get('interested', 0)} человек\n"
    text += f"📝 **Описание:** {telegram.get('description', '—')}\n"
    text += f"📝 **Bio:** {telegram.get('bio', '—')}\n\n"
    
    text += "👁️ **Глаз Исиды — OSINT**\n"
    text += "🛡️ @Arhapov"
    
    return text

# ==================== ГЕНЕРАЦИЯ HTML-ОТЧЁТА ====================

def generate_html_report(query: str, data: dict, report_id: str) -> str:
    sources = data.get("sources", {})
    qtype = data.get("type", "text")
    total = data.get("total_results", 0)
    
    if qtype in ["username", "telegram_id"]:
        telegram = sources.get("telegram", {})
        if telegram.get("found"):
            html = f"""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>👁️ Глаз Исиды — Telegram Профиль</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        @import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@400;700;900&display=swap');
        body {{
            background: #0a0a0a;
            color: #d0c0c0;
            font-family: 'Cinzel', serif;
            padding: 30px 20px;
        }}
        .container {{
            max-width: 800px;
            margin: 0 auto;
            background: #0d0a0a;
            border-radius: 16px;
            padding: 40px;
            border: 1px solid #3a1a1a;
        }}
        .profile-header {{
            display: flex;
            align-items: center;
            gap: 20px;
            border-bottom: 2px solid #3a1a1a;
            padding-bottom: 20px;
            margin-bottom: 20px;
        }}
        .avatar {{
            width: 100px;
            height: 100px;
            border-radius: 50%;
            background: #1a0a0a;
            border: 2px solid #cc3333;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 40px;
        }}
        .profile-info h1 {{
            color: #cc3333;
            font-size: 24px;
        }}
        .profile-info .username {{
            color: #6a4a4a;
            font-size: 16px;
        }}
        .info-block {{
            margin: 15px 0;
            padding: 15px;
            background: #0a0808;
            border-radius: 8px;
            border-left: 3px solid #cc3333;
        }}
        .info-block .label {{
            color: #6a4a4a;
            font-size: 12px;
            font-family: 'Courier New', monospace;
        }}
        .info-block .value {{
            color: #d0b0b0;
            font-size: 16px;
            margin-top: 4px;
        }}
        .footer {{
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #2a1212;
            font-size: 12px;
            color: #3a2a2a;
            text-align: center;
            font-family: 'Courier New', monospace;
        }}
        .badge {{
            display: inline-block;
            background: #1a0a0a;
            color: #cc3333;
            padding: 2px 12px;
            border-radius: 4px;
            font-size: 12px;
            border: 1px solid #3a1a1a;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="profile-header">
            <div class="avatar">👤</div>
            <div class="profile-info">
                <h1>{telegram.get('first_name', '—')} {telegram.get('last_name', '')}</h1>
                <div class="username">@{telegram.get('username', '—')}</div>
                <div><span class="badge">{'🤖 Бот' if telegram.get('is_bot') else '👤 Пользователь'}</span></div>
            </div>
        </div>
        
        <div class="info-block">
            <div class="label">🆔 TELEGRAM ID</div>
            <div class="value">{telegram.get('user_id', '—')}</div>
        </div>
        
        <div class="info-block">
            <div class="label">🗝️ ДАТА РЕГИСТРАЦИИ</div>
            <div class="value">{telegram.get('age', '≈ Неизвестно')}</div>
        </div>
        
        <div class="info-block">
            <div class="label">👮‍♂️ ИНТЕРЕСОВАЛИСЬ</div>
            <div class="value">{telegram.get('interested', 0)} человек</div>
        </div>
        
        <div class="info-block">
            <div class="label">📝 ОПИСАНИЕ</div>
            <div class="value">{telegram.get('description', '—')}</div>
        </div>
        
        <div class="info-block">
            <div class="label">📝 BIO</div>
            <div class="value">{telegram.get('bio', '—')}</div>
        </div>
        
        <div class="info-block">
            <div class="label">🔗 ССЫЛКА</div>
            <div class="value"><a href="{telegram.get('url', '#')}" style="color:#cc5555;">{telegram.get('url', '—')}</a></div>
        </div>
        
        <div class="footer">
            👁️ Глаз Исиды — OSINT · <a href="https://t.me/Arhapov" target="_blank">@Arhapov</a>
        </div>
    </div>
</body>
</html>
"""
            return html
    
    all_results = []
    for source_name, items in sources.items():
        if not items:
            continue
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    item['_source'] = source_name
                    all_results.append(item)
                else:
                    all_results.append({"title": str(item), "_source": source_name})
        elif isinstance(items, dict):
            item_copy = items.copy()
            item_copy['_source'] = source_name
            all_results.append(item_copy)
    
    display_results = all_results
    title = generate_title(query, qtype)
    
    html = f"""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        @import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@400;700;900&display=swap');
        body {{
            background: #0a0a0a;
            color: #d0c0c0;
            font-family: 'Cinzel', serif;
            padding: 30px 20px;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: #0d0a0a;
            border-radius: 16px;
            padding: 40px;
            border: 1px solid #3a1a1a;
        }}
        .watermark {{
            position: absolute;
            top: 25px;
            left: 30px;
            display: flex;
            flex-direction: column;
            align-items: center;
        }}
        .watermark svg {{
            width: 60px;
            height: 70px;
        }}
        .watermark .text {{
            color: #cc3333;
            font-size: 16px;
            font-weight: 900;
            letter-spacing: 6px;
            font-family: 'Cinzel', serif;
        }}
        .header {{
            border-bottom: 2px solid #3a1a1a;
            padding-bottom: 20px;
            margin-bottom: 28px;
            display: flex;
            justify-content: space-between;
            padding-left: 90px;
        }}
        .header h1 {{
            font-size: 28px;
            color: #cc3333;
            letter-spacing: 3px;
            font-family: 'Cinzel', serif;
        }}
        .header h1 span {{ color: #ff4444; background: #1a0a0a; padding: 0 14px; border-radius: 4px; border: 1px solid #3a1a1a; }}
        .header .sub {{ color: #7a4a4a; font-size: 13px; font-family: 'Courier New', monospace; }}
        .badge {{ background: #1a0a0a; padding: 6px 18px; border-radius: 8px; color: #cc3333; border: 1px solid #3a1a1a; }}
        .stats-bar {{
            display: flex;
            gap: 20px;
            margin: 20px 0;
            padding: 15px 20px;
            background: #0a0808;
            border-radius: 8px;
            border: 1px solid #2a1212;
        }}
        .stats-bar .stat {{ color: #6a3a3a; font-family: 'Courier New', monospace; }}
        .stats-bar .stat strong {{ color: #cc3333; }}
        .result-item {{
            margin: 14px 0;
            padding: 16px 22px;
            background: #0a0808;
            border-radius: 10px;
            border-left: 4px solid #3a1a1a;
            border: 1px solid #1a0a0a;
        }}
        .result-item:hover {{ background: #120a0a; border-left-color: #cc3333; border-color: #3a1a1a; }}
        .result-item .title {{ font-size: 17px; color: #d0b0b0; font-family: 'Cinzel', serif; }}
        .result-item .title a {{ color: #cc5555; text-decoration: none; border-bottom: 1px dotted #3a1a1a; }}
        .result-item .text {{ font-size: 14px; color: #8a6a6a; margin-top: 6px; font-family: 'Segoe UI', sans-serif; }}
        .result-item .extra {{ font-size: 13px; color: #6a4a4a; margin-top: 4px; font-family: 'Courier New', monospace; }}
        .result-item .index {{
            display: inline-block;
            background: #1a0a0a;
            color: #cc4444;
            font-size: 12px;
            padding: 2px 14px;
            border-radius: 6px;
            margin-right: 12px;
            border: 1px solid #2a1212;
        }}
        .source-tag {{
            background: #1a0a0a;
            color: #8a4a4a;
            font-size: 10px;
            padding: 2px 10px;
            border-radius: 4px;
            margin-left: 10px;
            border: 1px solid #2a1212;
            font-family: 'Courier New', monospace;
        }}
        .darknet-tag {{
            background: #1a0a1a;
            color: #aa44aa;
            font-size: 10px;
            padding: 2px 10px;
            border-radius: 4px;
            margin-left: 10px;
            border: 1px solid #3a1a3a;
            font-family: 'Courier New', monospace;
        }}
        .empty {{ color: #4a2a2a; font-style: italic; padding: 30px; text-align: center; border: 1px dashed #2a1212; border-radius: 8px; }}
        .footer {{
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #2a1212;
            font-size: 12px;
            color: #3a2a2a;
            text-align: center;
            font-family: 'Courier New', monospace;
        }}
        .footer a {{ color: #6a3a3a; text-decoration: none; border-bottom: 1px dotted #3a1a1a; }}
        .footer a:hover {{ color: #cc4444; }}
        .scanline {{
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            pointer-events: none;
            background: repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(200,0,0,0.005) 2px, rgba(200,0,0,0.005) 4px);
            z-index: 9999;
        }}
        @media (max-width: 600px) {{
            .container {{ padding: 16px; }}
            .header h1 {{ font-size: 20px; }}
            .watermark {{ display: none; }}
            .header {{ padding-left: 0; }}
            .stats-bar {{ flex-direction: column; gap: 6px; }}
        }}
    </style>
</head>
<body>
    <div class="scanline"></div>
    <div class="container">
        <div class="watermark">
            <svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
                <polygon points="50,5 5,85 95,85" stroke="#cc3333" stroke-width="2.5" fill="none"/>
                <circle cx="50" cy="45" r="14" stroke="#cc3333" stroke-width="2" fill="none"/>
                <circle cx="50" cy="45" r="5" fill="#cc3333"/>
                <circle cx="48" cy="43" r="2" fill="#ff6666" opacity="0.6"/>
                <path d="M32 28 L42 22" stroke="#cc3333" stroke-width="2.5" stroke-linecap="round"/>
                <path d="M68 28 L58 22" stroke="#cc3333" stroke-width="2.5" stroke-linecap="round"/>
                <text x="50" y="98" font-family="Cinzel, serif" font-size="10" fill="#cc3333" text-anchor="middle" letter-spacing="3">ГЛАЗ ИСИДЫ</text>
            </svg>
            <div class="text">ГЛАЗ ИСИДЫ</div>
        </div>
        <div class="header">
            <div>
                <h1>👁️ Глаз Исиды <span>OSINT</span></h1>
                <div class="sub">⚡ Запрос: {query} · Тип: {qtype} · {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</div>
                <div class="sub" style="color:#4a2a2a; margin-top:2px;">🛡️ Множество источников · Глубокий OSINT</div>
            </div>
            <div><span class="badge">🎯 НАЙДЕНО: {total}</span></div>
        </div>
        <div class="stats-bar">
            <span class="stat">📊 Всего результатов: <strong>{total}</strong></span>
            <span class="stat">🔍 Источников с данными: <strong>{len(display_results)}</strong></span>
            <span class="stat">⚡ Статус: <strong style="color:#cc3333;">АКТИВЕН</strong></span>
        </div>
"""
    
    if display_results:
        for idx, item in enumerate(display_results, 1):
            source = item.get('_source', '')
            title = item.get('title', '—')
            if isinstance(title, bool):
                title = "✅ Да" if title else "❌ Нет"
            title = str(title)[:80]
            text = item.get('text', '')[:250] if item.get('text') else ''
            extra = item.get('extra', '')
            link = item.get('link', '')
            
            details = []
            for key, value in item.items():
                if key in ['_source', 'title', 'text', 'extra', 'link', 'found', 'exists']:
                    continue
                if value and value != '—' and value != '':
                    if isinstance(value, str):
                        details.append(f"{key}: {value}")
                    elif isinstance(value, list):
                        details.append(f"{key}: {', '.join(str(v) for v in value[:5])}")
                    elif isinstance(value, dict):
                        for k, v in value.items():
                            if v and v != '—':
                                details.append(f"{key}.{k}: {v}")
            
            tag_class = 'source-tag'
            
            html += f"""
        <div class="result-item">
            <div class="title">
                <span class="index">#{idx}</span>
                {title}
                <span class="{tag_class}">{source[:12]}</span>
                {f'<a href="{link}" target="_blank">🔗</a>' if link else ''}
            </div>
"""
            if text:
                html += f"            <div class=\"text\">{text}</div>\n"
            if extra:
                html += f"            <div class=\"extra\">📎 {extra}</div>\n"
            if details:
                for detail in details[:8]:
                    html += f"            <div class=\"extra\">• {detail}</div>\n"
            html += "        </div>\n"
        
        html += f"""
        <div style="text-align:center; margin-top:20px; padding:12px; border:1px solid #2a1212; border-radius:8px; color:#5a3a3a; font-size:13px;">
            📊 Показано {len(display_results)} реальных результатов
        </div>
"""
    else:
        html += '<div class="empty">❌ Ничего не найдено</div>'
    
    html += f"""
        <div class="footer">
            👁️ Глаз Исиды — OSINT · <a href="https://t.me/Arhapov" target="_blank">@Arhapov</a>
        </div>
    </div>
</body>
</html>
"""
    return html

# ==================== СКРЫТИЕ ДАННЫХ ====================

@bot.callback_query_handler(func=lambda call: call.data == "hide_data")
def hide_data_callback(call):
    user_id = call.from_user.id
    try:
        bot.answer_callback_query(call.id)
    except:
        pass
    
    user_data = get_user_data(user_id)
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("📱 Отправить контакт", callback_data="send_contact_hide"),
        types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back")
    )
    
    safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        f"🔒 Скрытие данных\n\n"
        f"👤 Твой аккаунт:\n"
        f"🆔 ID: {user_id}\n"
        f"👤 Username: @{user_data['username']}\n"
        f"📛 Имя: {user_data['first_name']}\n\n"
        f"📌 Нажми кнопку ниже и отправь свой контакт.\n"
        f"Это нужно для получения номера телефона.\n\n"
        f"📝 После этого введи только ФИО для скрытия.",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data == "send_contact_hide")
def send_contact_hide(call):
    user_id = call.from_user.id
    try:
        bot.answer_callback_query(call.id)
    except:
        pass
    
    markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True, one_time_keyboard=True)
    contact_button = types.KeyboardButton("📱 Отправить контакт", request_contact=True)
    markup.add(contact_button)
    
    safe_send_message(
        user_id,
        "📱 Нажми кнопку ниже, чтобы отправить свой контакт.\n\n"
        "Это нужно для подтверждения твоей личности.",
        reply_markup=markup
    )
    user_state[user_id] = "awaiting_contact_for_hide"
    
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass

@bot.message_handler(content_types=['contact'])
def handle_contact(message):
    user_id = message.from_user.id
    
    if user_state.get(user_id) == "awaiting_contact_for_hide":
        contact = message.contact
        user_state.pop(user_id, None)
        
        username = message.from_user.username
        if not username:
            username = "нет"
        
        user_state[f"hide_contact_{user_id}"] = {
            "phone": contact.phone_number,
            "user_id": contact.user_id,
            "first_name": contact.first_name,
            "last_name": contact.last_name,
            "username": username
        }
        
        markup = types.ReplyKeyboardRemove()
        user_data = get_user_data(user_id)
        
        safe_send_message(
            user_id,
            f"✅ Контакт получен!\n\n"
            f"📱 Номер: {contact.phone_number}\n"
            f"👤 Username: @{username}\n"
            f"🆔 ID: {user_id}\n\n"
            f"📝 Теперь отправь ФИО для скрытия:\n\n"
            f"Пример: Иванов Иван Иванович",
            reply_markup=markup
        )
        user_state[user_id] = "awaiting_hide_fio"

@bot.message_handler(func=lambda message: user_state.get(message.from_user.id) == "awaiting_hide_fio")
def process_hide_fio(message):
    user_id = message.from_user.id
    fio = message.text.strip()
    user_state.pop(user_id, None)
    
    if not re.search(r'^[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?$', fio):
        safe_send_message(
            user_id,
            "❌ Некорректное ФИО\n\n"
            "Формат должен быть: Иванов Иван Иванович\n"
            "Попробуй ещё раз."
        )
        user_state[user_id] = "awaiting_hide_fio"
        return
    
    contact = user_state.get(f"hide_contact_{user_id}", {})
    
    username = message.from_user.username
    if not username:
        username = "нет"
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO hide_requests (
                user_id, username, contact_phone, phone, fio, status
            ) VALUES (?, ?, ?, ?, ?, 'pending')
        ''', (
            user_id,
            username,
            contact.get('phone') or '—',
            contact.get('phone') or '—',
            fio
        ))
        request_id = cur.lastrowid
        conn.commit()
        conn.close()
        
        admin_text = (
            f"🔔 Новая заявка на скрытие данных #{request_id}\n\n"
            f"👤 Пользователь: @{username} | {user_id}\n"
            f"📱 Контактный телефон: {contact.get('phone') or '—'}\n"
            f"👤 ФИО для скрытия: {fio}"
        )
        
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_hide_{request_id}"),
            types.InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_hide_{request_id}")
        )
        safe_send_message(ADMIN_ID, admin_text, reply_markup=markup)
        
        safe_send_message(
            user_id,
            f"✅ Заявка отправлена!\n\n"
            f"📋 ФИО для скрытия: {fio}\n"
            f"📱 Телефон: {contact.get('phone') or '—'}\n"
            f"👤 Username: @{username}\n"
            f"🆔 ID: {user_id}\n\n"
            f"⏳ Ожидай решения администратора."
        )
    except Exception as e:
        safe_send_message(user_id, f"⚠️ Ошибка: {str(e)[:100]}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('approve_hide_'))
def approve_hide(call):
    if call.from_user.id != ADMIN_ID:
        try:
            bot.answer_callback_query(call.id, "❌ Только для админа!", show_alert=True)
        except:
            pass
        return
    
    request_id = int(call.data.split('_')[2])
    try:
        bot.answer_callback_query(call.id, "✅ Заявка одобрена!")
    except:
        pass
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute('''
            SELECT user_id, username, phone, fio FROM hide_requests WHERE id = ?
        ''', (request_id,))
        row = cur.fetchone()
        if not row:
            safe_send_message(ADMIN_ID, "❌ Заявка не найдена.")
            return
        
        user_id, username, phone, fio = row
        
        cur.execute('''
            INSERT OR REPLACE INTO hidden_data (user_id, username, phone, fio)
            VALUES (?, ?, ?, ?)
        ''', (user_id, username, phone, fio))
        
        cur.execute("UPDATE hide_requests SET status = 'approved', reviewed_by = ? WHERE id = ?", (ADMIN_ID, request_id))
        conn.commit()
        conn.close()
        
        safe_send_message(
            user_id,
            f"✅ Ваша заявка на скрытие данных ОДОБРЕНА!\n\n"
            f"🔒 Теперь эти данные скрыты от поиска:\n"
            f"📱 Телефон: {phone or '—'}\n"
            f"👤 ФИО: {fio}\n"
            f"👤 Username: @{username}\n"
            f"🆔 ID: {user_id}"
        )
        safe_send_message(ADMIN_ID, f"✅ Заявка #{request_id} одобрена.")
    except Exception as e:
        safe_send_message(ADMIN_ID, f"⚠️ Ошибка: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('reject_hide_'))
def reject_hide(call):
    if call.from_user.id != ADMIN_ID:
        try:
            bot.answer_callback_query(call.id, "❌ Только для админа!", show_alert=True)
        except:
            pass
        return
    
    request_id = int(call.data.split('_')[2])
    try:
        bot.answer_callback_query(call.id, "❌ Заявка отклонена!")
    except:
        pass
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM hide_requests WHERE id = ?", (request_id,))
        row = cur.fetchone()
        if row:
            user_id = row[0]
            cur.execute("UPDATE hide_requests SET status = 'rejected', reviewed_by = ? WHERE id = ?", (ADMIN_ID, request_id))
            conn.commit()
            safe_send_message(
                user_id,
                "❌ Ваша заявка на скрытие данных ОТКЛОНЕНА\n\n"
                "📌 Возможно, данные не соответствуют требованиям.\n"
                "🔄 Попробуй отправить заявку ещё раз с корректными данными."
            )
        conn.close()
        safe_send_message(ADMIN_ID, f"❌ Заявка #{request_id} отклонена.")
    except Exception as e:
        safe_send_message(ADMIN_ID, f"⚠️ Ошибка: {e}")

@bot.callback_query_handler(func=lambda call: call.data == "list_hide_requests")
def list_hide_requests(call):
    if call.from_user.id != ADMIN_ID:
        try:
            bot.answer_callback_query(call.id, "❌ Только для админа!", show_alert=True)
        except:
            pass
        return
    
    try:
        bot.answer_callback_query(call.id)
    except:
        pass
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT id, user_id, username, phone, fio, created_at FROM hide_requests WHERE status = 'pending' ORDER BY created_at DESC")
        rows = cur.fetchall()
        conn.close()
        
        if not rows:
            safe_send_message(ADMIN_ID, "📋 Нет активных заявок.")
            return
        
        text = "📋 Заявки на скрытие данных\n\n"
        for row in rows[:10]:
            req_id, user_id, username, phone, fio, created = row
            text += (
                f"🔹 #{req_id} | @{username or 'нет'} | {user_id}\n"
                f"   📱 {phone or '—'} | 👤 {fio or '—'}\n"
                f"   🕐 {created[:16]}\n\n"
            )
        
        markup = types.InlineKeyboardMarkup()
        for row in rows[:5]:
            req_id = row[0]
            markup.add(
                types.InlineKeyboardButton(f"✅ #{req_id}", callback_data=f"approve_hide_{req_id}"),
                types.InlineKeyboardButton(f"❌ #{req_id}", callback_data=f"reject_hide_{req_id}")
            )
        markup.add(types.InlineKeyboardButton("🔄 Обновить", callback_data="list_hide_requests"))
        
        safe_send_message(ADMIN_ID, text, reply_markup=markup)
    except Exception as e:
        safe_send_message(ADMIN_ID, f"⚠️ Ошибка: {e}")

# ==================== АДМИН-КОМАНДЫ ====================

@bot.message_handler(commands=['adminhelp'])
def admin_help_command(message):
    if message.from_user.id != ADMIN_ID:
        safe_send_message(message.chat.id, "❌ Только для админа.")
        return
    
    help_text = (
        "👑 Админ-команды Глаз Исиды\n\n"
        "📌 Управление пользователями\n"
        "/give <кол-во> <user_id> — выдать запросы\n"
        "/take <кол-во> <user_id> — забрать запросы\n"
        "/users — список пользователей\n"
        "/hide <user_id> — раскрыть скрытые данные\n\n"
        "📌 Управление ботом\n"
        "/tech on/off — техперерыв\n"
        "/stats — статистика бота\n"
        "/broadcast <текст> — рассылка\n\n"
        "📌 Скрытие данных\n"
        "/requests — список заявок на скрытие\n"
        "/hide <user_id> — раскрыть данные\n\n"
        "👁️ Глаз Исиды — OSINT\n"
        "🛡️ @Arhapov"
    )
    safe_send_message(message.chat.id, help_text)

@bot.message_handler(commands=['tech'])
def tech_command(message):
    global TECH_MODE
    if message.from_user.id != ADMIN_ID:
        safe_send_message(message.chat.id, "❌ Только для админа.")
        return
    args = message.text.split()
    if len(args) < 2:
        safe_send_message(message.chat.id, "❗ /tech on/off")
        return
    if args[1].lower() == "on":
        TECH_MODE = True
        safe_send_message(message.chat.id, "🔧 Техперерыв ВКЛЮЧЁН")
    elif args[1].lower() == "off":
        TECH_MODE = False
        safe_send_message(message.chat.id, "✅ Техперерыв ВЫКЛЮЧЕН")
    else:
        safe_send_message(message.chat.id, "❗ /tech on или /tech off")

@bot.message_handler(commands=['stats'])
def stats_command(message):
    if message.from_user.id != ADMIN_ID:
        safe_send_message(message.chat.id, "❌ Только для админа.")
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users")
        total_users = cur.fetchone()[0]
        cur.execute("SELECT SUM(searches_today + searches_extra) FROM users")
        total_searches = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM hide_requests WHERE status = 'pending'")
        pending_requests = cur.fetchone()[0] or 0
        conn.close()
        safe_send_message(
            message.chat.id,
            f"📊 Статистика бота\n\n"
            f"👥 Всего пользователей: {total_users}\n"
            f"🔍 Всего поисков: {total_searches}\n"
            f"📋 Заявок на скрытие: {pending_requests}\n"
            f"👑 Админ: @Arhapov"
        )
    except Exception as e:
        safe_send_message(message.chat.id, f"⚠️ Ошибка: {str(e)[:100]}")

@bot.message_handler(commands=['broadcast'])
def broadcast_command(message):
    if message.from_user.id != ADMIN_ID:
        safe_send_message(message.chat.id, "❌ Только для админа.")
        return
    text = message.text.replace('/broadcast', '').strip()
    if not text:
        safe_send_message(message.chat.id, "❗ /broadcast <текст сообщения>")
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM users")
        users = cur.fetchall()
        conn.close()
        sent = 0
        for user in users:
            try:
                safe_send_message(user[0], f"📢 Рассылка\n\n{text}")
                sent += 1
                time.sleep(0.05)
            except:
                continue
        safe_send_message(message.chat.id, f"✅ Рассылка отправлена {sent} пользователям.")
    except Exception as e:
        safe_send_message(message.chat.id, f"⚠️ Ошибка: {str(e)[:100]}")

@bot.message_handler(commands=['requests'])
def requests_command(message):
    if message.from_user.id != ADMIN_ID:
        safe_send_message(message.chat.id, "❌ Только для админа.")
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT id, user_id, username, phone, fio, created_at FROM hide_requests WHERE status = 'pending' ORDER BY created_at DESC")
        rows = cur.fetchall()
        conn.close()
        if not rows:
            safe_send_message(message.chat.id, "📋 Нет активных заявок.")
            return
        text = "📋 Заявки на скрытие данных\n\n"
        for row in rows[:10]:
            req_id, user_id, username, phone, fio, created = row
            text += (
                f"🔹 #{req_id} | @{username or 'нет'} | {user_id}\n"
                f"   📱 {phone or '—'} | 👤 {fio or '—'}\n"
                f"   🕐 {created[:16]}\n\n"
            )
        markup = types.InlineKeyboardMarkup()
        for row in rows[:5]:
            req_id = row[0]
            markup.add(
                types.InlineKeyboardButton(f"✅ #{req_id}", callback_data=f"approve_hide_{req_id}"),
                types.InlineKeyboardButton(f"❌ #{req_id}", callback_data=f"reject_hide_{req_id}")
            )
        markup.add(types.InlineKeyboardButton("🔄 Обновить", callback_data="list_hide_requests"))
        safe_send_message(message.chat.id, text, reply_markup=markup)
    except Exception as e:
        safe_send_message(message.chat.id, f"⚠️ Ошибка: {str(e)[:100]}")

@bot.message_handler(commands=['hide'])
def hide_command(message):
    if message.from_user.id != ADMIN_ID:
        safe_send_message(message.chat.id, "❌ Только для админа.")
        return
    args = message.text.split()
    if len(args) < 2:
        safe_send_message(message.chat.id, "❗ /hide <user_id>")
        return
    target_id = int(args[1])
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("DELETE FROM hidden_data WHERE user_id = ?", (target_id,))
        conn.commit()
        conn.close()
        safe_send_message(
            message.chat.id,
            f"✅ Данные пользователя {target_id} раскрыты.\n\n🔓 Теперь его данные снова видны в поиске."
        )
    except Exception as e:
        safe_send_message(message.chat.id, f"⚠️ Ошибка: {str(e)[:100]}")

@bot.message_handler(commands=['give'])
def give_command(message):
    if message.from_user.id != ADMIN_ID:
        safe_send_message(message.chat.id, "❌ Только для админа.")
        return
    try:
        args = message.text.split()
        if len(args) < 3:
            safe_send_message(message.chat.id, "❗ /give <кол-во> <user_id>")
            return
        amount = int(args[1])
        target_id = int(args[2])
        user = get_user(target_id)
        user["searches_today"] = max(0, user["searches_today"] - amount)
        update_user(target_id, user)
        safe_send_message(message.chat.id, f"✅ Выдано {amount} запросов пользователю {target_id}.")
    except:
        safe_send_message(message.chat.id, "⚠️ Ошибка. /give <кол-во> <user_id>")

@bot.message_handler(commands=['take'])
def take_command(message):
    if message.from_user.id != ADMIN_ID:
        safe_send_message(message.chat.id, "❌ Только для админа.")
        return
    try:
        args = message.text.split()
        if len(args) < 3:
            safe_send_message(message.chat.id, "❗ /take <кол-во> <user_id>")
            return
        amount = int(args[1])
        target_id = int(args[2])
        user = get_user(target_id)
        user["searches_extra"] = max(0, user["searches_extra"] - amount)
        update_user(target_id, user)
        safe_send_message(message.chat.id, f"✅ Забрано {amount} запросов у пользователя {target_id}.")
    except:
        safe_send_message(message.chat.id, "⚠️ Ошибка. /take <кол-во> <user_id>")

@bot.message_handler(commands=['users'])
def users_command(message):
    if message.from_user.id != ADMIN_ID:
        safe_send_message(message.chat.id, "❌ Только для админа.")
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT user_id, username, searches_today, searches_extra FROM users ORDER BY searches_today DESC")
        rows = cur.fetchall()
        conn.close()
        if not rows:
            safe_send_message(message.chat.id, "📊 Нет пользователей.")
            return
        text = "📊 Список пользователей\n\n"
        for user_id, username, today, extra in rows[:20]:
            total = (5 - today) + extra
            text += f"• {user_id} — @{username or 'нет'} | запросов: {total}\n"
        safe_send_message(message.chat.id, text)
    except Exception as e:
        safe_send_message(message.chat.id, f"⚠️ Ошибка: {str(e)[:100]}")

# ==================== ФУНКЦИЯ ВЫПОЛНЕНИЯ ПОИСКА ====================

def run_search_sync(chat_id, user_id, query, mode):
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(perform_search(chat_id, user_id, query, mode))
        finally:
            loop.close()
    except Exception as e:
        logger.error(f"Sync search error: {e}")
        safe_send_message(chat_id, f"⚠️ Ошибка: {str(e)[:100]}")

async def perform_search(chat_id, user_id, query, mode):
    try:
        if not can_search(user_id):
            safe_send_message(chat_id, "❌ Лимит поисков исчерпан!\n\n⏰ Сброс в 00:00 МСК")
            return
        
        if mode == "global":
            await run_global_search(chat_id, user_id, query)
        elif mode == "phone":
            await run_phone_search(chat_id, user_id, query)
        elif mode == "telegram_id":
            await run_telegram_id_search(chat_id, user_id, query)
        elif mode == "ip":
            await run_ip_search(chat_id, user_id, query)
        elif mode == "domain":
            await run_domain_search(chat_id, user_id, query)
        elif mode == "username":
            await run_username_search(chat_id, user_id, query)
        else:
            safe_send_message(chat_id, "❌ Неизвестный режим поиска.")
    except Exception as e:
        logger.error(f"Perform search error: {e}")
        safe_send_message(chat_id, f"⚠️ Ошибка: {str(e)[:100]}")

async def run_global_search(chat_id, user_id, query):
    start_time = time.time()
    msg = safe_send_message(chat_id, "👁️ Глаз Исиды — глубокое сканирование...\n⏱️ Время: до 120 секунд\n🕵️ Множество источников...")
    
    try:
        data = await asyncio.wait_for(global_lookup(query), timeout=120)
    except asyncio.TimeoutError:
        safe_edit_message(chat_id, msg.message_id, "⚠️ Поиск прерван по таймауту (120 секунд)\n\n📌 Показаны только быстрые результаты.")
        data = {"query": query, "type": "unknown", "sources": {}, "total_results": 0}
    
    elapsed = time.time() - start_time
    total = data.get("total_results", 0)
    remaining = use_search(user_id)
    
    if data.get("type") in ["username", "telegram_id"]:
        text = format_telegram_result(data)
        if data.get("hidden"):
            text = "🔒 Данные скрыты по запросу владельца"
        safe_send_message(chat_id, text, parse_mode="Markdown")
        try:
            bot.delete_message(chat_id, msg.message_id)
        except:
            pass
        return
    
    report_id = f"{user_id}_{int(datetime.now().timestamp())}"
    html = generate_html_report(query, data, report_id)
    reports[report_id] = {"query": query, "data": data, "html": html, "created": datetime.now().timestamp()}
    
    filename = f"{user_id}_{int(datetime.now().timestamp())}.html"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html)
    
    with open(filename, "rb") as f:
        caption = f"👁️ OSINT-ОТЧЁТ\n\n🔍 Запрос: {query}\n📌 Найдено: {total}\n🔍 Осталось: {remaining}/5\n⏱️ Время: {elapsed:.1f} сек\n🛡️ @Arhapov"
        if len(caption) > 1000:
            caption = caption[:997] + "..."
        bot.send_document(chat_id, f, caption=caption, parse_mode=None)
    
    os.remove(filename)
    try:
        bot.delete_message(chat_id, msg.message_id)
    except:
        pass

async def run_phone_search(chat_id, user_id, query):
    start_time = time.time()
    msg = safe_send_message(chat_id, "📱 Поиск по номеру телефона...\n⏱️ Время: до 60 секунд")
    
    try:
        data = await global_lookup(query)
    except Exception as e:
        safe_edit_message(chat_id, msg.message_id, f"⚠️ Ошибка: {str(e)[:100]}")
        return
    
    elapsed = time.time() - start_time
    total = data.get("total_results", 0)
    remaining = use_search(user_id)
    
    report_id = f"{user_id}_{int(datetime.now().timestamp())}"
    html = generate_html_report(query, data, report_id)
    reports[report_id] = {"query": query, "data": data, "html": html, "created": datetime.now().timestamp()}
    
    filename = f"{user_id}_{int(datetime.now().timestamp())}.html"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html)
    
    with open(filename, "rb") as f:
        caption = f"📱 ОТЧЁТ ПО НОМЕРУ\n\n🔍 Запрос: {query}\n📌 Найдено: {total}\n🔍 Осталось: {remaining}/5\n⏱️ Время: {elapsed:.1f} сек\n🛡️ @Arhapov"
        if len(caption) > 1000:
            caption = caption[:997] + "..."
        bot.send_document(chat_id, f, caption=caption, parse_mode=None)
    
    os.remove(filename)
    try:
        bot.delete_message(chat_id, msg.message_id)
    except:
        pass

async def run_telegram_id_search(chat_id, user_id, query):
    start_time = time.time()
    msg = safe_send_message(chat_id, "🆔 Поиск по Telegram ID...\n⏱️ Время: до 10 секунд")
    
    try:
        data = await search_telegram_id(query)
    except Exception as e:
        safe_edit_message(chat_id, msg.message_id, f"⚠️ Ошибка: {str(e)[:100]}")
        return
    
    remaining = use_search(user_id)
    elapsed = time.time() - start_time
    
    if data.get("hidden"):
        safe_edit_message(chat_id, msg.message_id, "🔒 Данные скрыты по запросу владельца")
        return
    
    text = format_telegram_result(data)
    text += f"\n\n⏱️ Время: {elapsed:.1f} сек\n🔍 Осталось: {remaining}/5"
    safe_edit_message(chat_id, msg.message_id, text, parse_mode="Markdown")

async def run_ip_search(chat_id, user_id, query):
    start_time = time.time()
    msg = safe_send_message(chat_id, "🌐 Поиск по IP-адресу...\n⏱️ Время: до 30 секунд")
    
    try:
        data = await global_lookup(query)
    except Exception as e:
        safe_edit_message(chat_id, msg.message_id, f"⚠️ Ошибка: {str(e)[:100]}")
        return
    
    elapsed = time.time() - start_time
    total = data.get("total_results", 0)
    remaining = use_search(user_id)
    
    report_id = f"{user_id}_{int(datetime.now().timestamp())}"
    html = generate_html_report(query, data, report_id)
    reports[report_id] = {"query": query, "data": data, "html": html, "created": datetime.now().timestamp()}
    
    filename = f"{user_id}_{int(datetime.now().timestamp())}.html"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html)
    
    with open(filename, "rb") as f:
        caption = f"🌐 IP-ОТЧЁТ\n\n🔍 Запрос: {query}\n📌 Найдено: {total}\n🔍 Осталось: {remaining}/5\n⏱️ Время: {elapsed:.1f} сек\n🛡️ @Arhapov"
        if len(caption) > 1000:
            caption = caption[:997] + "..."
        bot.send_document(chat_id, f, caption=caption, parse_mode=None)
    
    os.remove(filename)
    try:
        bot.delete_message(chat_id, msg.message_id)
    except:
        pass

async def run_domain_search(chat_id, user_id, query):
    start_time = time.time()
    msg = safe_send_message(chat_id, "🌐 Поиск по домену...\n⏱️ Время: до 30 секунд")
    
    try:
        data = await global_lookup(query)
    except Exception as e:
        safe_edit_message(chat_id, msg.message_id, f"⚠️ Ошибка: {str(e)[:100]}")
        return
    
    elapsed = time.time() - start_time
    total = data.get("total_results", 0)
    remaining = use_search(user_id)
    
    report_id = f"{user_id}_{int(datetime.now().timestamp())}"
    html = generate_html_report(query, data, report_id)
    reports[report_id] = {"query": query, "data": data, "html": html, "created": datetime.now().timestamp()}
    
    filename = f"{user_id}_{int(datetime.now().timestamp())}.html"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html)
    
    with open(filename, "rb") as f:
        caption = f"🌐 ДОМЕН-ОТЧЁТ\n\n🔍 Запрос: {query}\n📌 Найдено: {total}\n🔍 Осталось: {remaining}/5\n⏱️ Время: {elapsed:.1f} сек\n🛡️ @Arhapov"
        if len(caption) > 1000:
            caption = caption[:997] + "..."
        bot.send_document(chat_id, f, caption=caption, parse_mode=None)
    
    os.remove(filename)
    try:
        bot.delete_message(chat_id, msg.message_id)
    except:
        pass

async def run_username_search(chat_id, user_id, query):
    start_time = time.time()
    msg = safe_send_message(chat_id, "👤 Поиск по username...\n⏱️ Время: до 10 секунд")
    
    try:
        data = await search_username(query)
    except Exception as e:
        safe_edit_message(chat_id, msg.message_id, f"⚠️ Ошибка: {str(e)[:100]}")
        return
    
    remaining = use_search(user_id)
    elapsed = time.time() - start_time
    
    if data.get("hidden"):
        safe_edit_message(chat_id, msg.message_id, "🔒 Данные скрыты по запросу владельца")
        return
    
    text = format_telegram_result(data)
    text += f"\n\n⏱️ Время: {elapsed:.1f} сек\n🔍 Осталось: {remaining}/5"
    safe_edit_message(chat_id, msg.message_id, text, parse_mode="Markdown")

# ==================== МЕНЮ ====================

def main_menu_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    markup.add(
        types.InlineKeyboardButton("🌍 ГЛОБАЛЬНЫЙ", callback_data="global_search"),
        types.InlineKeyboardButton("📱 TELEGRAM", callback_data="telegram_search")
    )
    
    markup.add(
        types.InlineKeyboardButton("📞 ТЕЛЕФОН", callback_data="phone_search"),
        types.InlineKeyboardButton("📧 EMAIL", callback_data="email_search")
    )
    
    markup.add(
        types.InlineKeyboardButton("👤 USERNAME", callback_data="username_search"),
        types.InlineKeyboardButton("🆔 TELEGRAM ID", callback_data="telegram_id_search")
    )
    
    markup.add(
        types.InlineKeyboardButton("🌐 IP", callback_data="ip_search"),
        types.InlineKeyboardButton("🌍 ДОМЕН", callback_data="domain_search")
    )
    
    markup.add(
        types.InlineKeyboardButton("🌍 ГЕОИНТ", callback_data="geoint_search"),
        types.InlineKeyboardButton("🖼️ МЕТАДАННЫЕ", callback_data="metadata_search")
    )
    
    markup.add(
        types.InlineKeyboardButton("🔒 СКРЫТЬ ДАННЫЕ", callback_data="hide_data"),
        types.InlineKeyboardButton("⚡ ПРОФИЛЬ", callback_data="menu_profile")
    )
    
    markup.add(
        types.InlineKeyboardButton("📊 БАЛАНС", callback_data="menu_balance"),
        types.InlineKeyboardButton("❓ ПОМОЩЬ", callback_data="menu_help")
    )
    
    markup.add(
        types.InlineKeyboardButton("🛡️ КАНАЛ", url="https://t.me/Arhapov")
    )
    
    return markup

# ==================== ГЛОБАЛЬНЫЙ ОБРАБОТЧИК КОЛБЭКОВ ====================

@bot.callback_query_handler(func=lambda call: True)
def global_callback_handler(call):
    try:
        try:
            bot.answer_callback_query(call.id)
        except:
            pass
        
        data = call.data
        
        if data.startswith('search_'):
            user_id = call.from_user.id
            query = user_state.get(f"search_query_{user_id}", "")
            mode = data.replace('search_', '')
            user_search_mode[user_id] = mode
            run_search_sync(call.message.chat.id, user_id, query, mode)
            return
        
        if data == "telegram_search":
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("👤 По username", callback_data="telegram_username_mode"),
                types.InlineKeyboardButton("🆔 По ID", callback_data="telegram_id_mode")
            )
            markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back"))
            safe_edit_message(
                call.message.chat.id,
                call.message.message_id,
                "📱 **ПОИСК В TELEGRAM**\n\nВыбери способ поиска:",
                reply_markup=markup,
                parse_mode="Markdown"
            )
            return
        
        if data == "telegram_username_mode":
            user_search_mode[call.from_user.id] = "username"
            safe_edit_message(
                call.message.chat.id,
                call.message.message_id,
                "👤 **ПОИСК ПО USERNAME**\n\nОтправь username Telegram аккаунта.\n\nПример: @durov или durov",
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back")
                ),
                parse_mode="Markdown"
            )
            return
        
        if data == "telegram_id_mode":
            user_search_mode[call.from_user.id] = "telegram_id"
            safe_edit_message(
                call.message.chat.id,
                call.message.message_id,
                "🆔 **ПОИСК ПО TELEGRAM ID**\n\nОтправь числовой ID Telegram аккаунта.\n\nПример: 123456789",
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back")
                ),
                parse_mode="Markdown"
            )
            return
        
        if data == "menu_back":
            safe_edit_message(
                call.message.chat.id,
                call.message.message_id,
                "👁️ **Глаз Исиды — OSINT**\n\n🕵️ Глубокий OSINT-поиск\n🛡️ Множество источников\n🔒 Скрытие данных\n\n⚡ **Выбери действие:**",
                reply_markup=main_menu_keyboard(),
                parse_mode="Markdown"
            )
            return
        
        if data == "menu_profile":
            user = call.from_user
            user_data = get_user(user.id, user.username or "Unknown")
            remaining = get_remaining(user.id)
            text = (
                "👤 **Твой профиль**\n\n"
                f"🆔 ID: `{user.id}`\n"
                f"👤 Username: @{user.username or 'нет'}\n"
                f"📛 Имя: {user.first_name or '—'}\n\n"
                f"📊 Использовано: {user_data['searches_today']}/5\n"
                f"📊 Бонусных: {user_data['searches_extra']}\n"
                f"📊 Осталось: {remaining}\n"
                f"⏰ Сброс: в 00:00 МСК\n"
                f"👑 Админ: {'✅' if user.id == ADMIN_ID else '❌'}"
            )
            safe_edit_message(
                call.message.chat.id,
                call.message.message_id,
                text,
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back")
                ),
                parse_mode="Markdown"
            )
            return
        
        if data == "menu_balance":
            user_id = call.from_user.id
            remaining = get_remaining(user_id)
            used = get_user(user_id)["searches_today"]
            extra = get_user(user_id)["searches_extra"]
            text = (
                "📊 **Твой баланс**\n\n"
                f"🔍 Использовано: {used}/5\n"
                f"📊 Бонусных: {extra}\n"
                f"📊 Осталось: {remaining}\n"
                f"⏰ Сброс: в 00:00 МСК\n"
                f"👑 Админ: {'♾️ безлимитный' if user_id == ADMIN_ID else 'нет'}"
            )
            safe_edit_message(
                call.message.chat.id,
                call.message.message_id,
                text,
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back")
                ),
                parse_mode="Markdown"
            )
            return
        
        if data == "menu_help":
            safe_edit_message(
                call.message.chat.id,
                call.message.message_id,
                "❓ **Помощь**\n\n📌 **Как пользоваться:**\n• Отправь номер, email, никнейм, IP или домен\n• Выбери тип поиска\n\n📊 **Лимит:** 5 поисков в день\n👑 **Админ:** безлимитный доступ\n🔒 **Скрытие данных:** отправь заявку админу\n\n🛡️ @Arhapov",
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back")
                ),
                parse_mode="Markdown"
            )
            return
        
        if data == "global_search":
            user_search_mode[call.from_user.id] = "global"
            safe_edit_message(
                call.message.chat.id,
                call.message.message_id,
                "🌐 **ГЛОБАЛЬНЫЙ ПОИСК**\n\nОтправь запрос для поиска:\n• 📱 Номер: +79991234567\n• 👤 ФИО: Иванов Иван Иванович\n• 📧 Email: user@example.com\n• 🌐 IP: 8.8.8.8\n• 🌍 Домен: example.com",
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back")
                ),
                parse_mode="Markdown"
            )
            return
        
        if data == "phone_search":
            user_search_mode[call.from_user.id] = "phone"
            safe_edit_message(
                call.message.chat.id,
                call.message.message_id,
                "📞 **ПРОВЕРКА ТЕЛЕФОНА**\n\nОтправь номер для проверки.\n\nПример: +79991234567",
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back")
                ),
                parse_mode="Markdown"
            )
            return
        
        if data == "email_search":
            user_search_mode[call.from_user.id] = "global"
            safe_edit_message(
                call.message.chat.id,
                call.message.message_id,
                "📧 **ПРОВЕРКА EMAIL**\n\nОтправь email для проверки.\n\nПример: user@example.com",
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back")
                ),
                parse_mode="Markdown"
            )
            return
        
        if data == "username_search":
            user_search_mode[call.from_user.id] = "username"
            safe_edit_message(
                call.message.chat.id,
                call.message.message_id,
                "👤 **ПОИСК ПО USERNAME**\n\nОтправь username для поиска.\n\nПример: username",
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back")
                ),
                parse_mode="Markdown"
            )
            return
        
        if data == "telegram_id_search":
            user_search_mode[call.from_user.id] = "telegram_id"
            safe_edit_message(
                call.message.chat.id,
                call.message.message_id,
                "🆔 **ПОИСК ПО TELEGRAM ID**\n\nОтправь числовой ID Telegram аккаунта.\n\nПример: 123456789",
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back")
                ),
                parse_mode="Markdown"
            )
            return
        
        if data == "ip_search":
            user_search_mode[call.from_user.id] = "ip"
            safe_edit_message(
                call.message.chat.id,
                call.message.message_id,
                "🌐 **ПОИСК ПО IP**\n\nОтправь IP-адрес.\n\nПример: 8.8.8.8",
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back")
                ),
                parse_mode="Markdown"
            )
            return
        
        if data == "domain_search":
            user_search_mode[call.from_user.id] = "domain"
            safe_edit_message(
                call.message.chat.id,
                call.message.message_id,
                "🌍 **ПОИСК ПО ДОМЕНУ**\n\nОтправь домен.\n\nПример: example.com",
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back")
                ),
                parse_mode="Markdown"
            )
            return
        
        if data == "geoint_search":
            safe_edit_message(
                call.message.chat.id,
                call.message.message_id,
                "🌍 **ГЕОИНТ**\n\nОтправь координаты или адрес.\n\nПример: 55.7558,37.6173",
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back")
                ),
                parse_mode="Markdown"
            )
            return
        
        if data == "metadata_search":
            safe_edit_message(
                call.message.chat.id,
                call.message.message_id,
                "🖼️ **МЕТАДАННЫЕ ФОТО**\n\nОтправь ссылку на фото для извлечения EXIF-данных.",
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back")
                ),
                parse_mode="Markdown"
            )
            return
        
    except Exception as e:
        logger.error(f"❌ Global callback error: {e}")

# ==================== ОБРАБОТЧИК ТЕКСТА (С ФИЛЬТРОМ - НЕ КОМАНДЫ) ====================

# Функция-фильтр: пропускаем только сообщения, которые НЕ начинаются с /
def is_not_command(message):
    return not message.text.startswith('/')

@bot.message_handler(func=is_not_command)
def handle_text(message):
    try:
        text = message.text.strip()
        
        if not text:
            return
        
        if TECH_MODE and message.from_user.id != ADMIN_ID:
            safe_send_message(message.chat.id, "🔧 Бот на техническом обслуживании\n\n⏰ Вернёмся через несколько минут!")
            return
        
        user_id = message.from_user.id
        chat_id = message.chat.id
        
        if user_search_mode.get(user_id):
            mode = user_search_mode.pop(user_id)
            run_search_sync(chat_id, user_id, text, mode)
            return
        
        if check_hidden_data(text):
            safe_send_message(chat_id, "🔒 Человек скрыл свои данные\n\nДанные этого пользователя скрыты по его запросу.\n\n🛡️ @Arhapov")
            return
        
        is_digits = re.match(r'^[\d\s\-()+.]+$', text)
        
        if is_digits:
            markup = get_choice_keyboard_for_numbers()
            user_state[f"search_query_{user_id}"] = text
            safe_send_message(chat_id, "📌 Выберите тип функции для поиска:", reply_markup=markup)
        else:
            markup = get_choice_keyboard_for_text()
            user_state[f"search_query_{user_id}"] = text
            safe_send_message(chat_id, "📌 Выберите тип функции для поиска:", reply_markup=markup)
        
    except Exception as e:
        logger.error(f"Handle text error: {e}")
        safe_send_message(message.chat.id, f"⚠️ Ошибка: {str(e)[:100]}")

# ==================== ОСНОВНЫЕ КОМАНДЫ ====================

@bot.message_handler(commands=['start'])
def start_command(message):
    try:
        logger.info(f"Start command from {message.from_user.id}")
        remaining = get_remaining(message.from_user.id)
        user_id = message.from_user.id
        user_state.pop(user_id, None)
        user_state.pop(f"search_query_{user_id}", None)
        user_search_mode.pop(user_id, None)
        
        safe_send_message(
            message.chat.id,
            f"👁️ **Глаз Исиды — OSINT**\n\n"
            f"🕵️ Привет, {message.from_user.first_name}!\n"
            f"⚡ Глубокий OSINT-поиск\n"
            f"🛡️ Множество источников\n"
            f"🔒 Защита скрытых данных\n\n"
            f"📊 Осталось: {remaining}/5\n\n"
            f"📌 **Выбери действие:**",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Start error: {e}")
        safe_send_message(message.chat.id, f"⚠️ Ошибка: {str(e)[:100]}")

# ==================== ЗАПУСК ====================

if __name__ == "__main__":
    init_db()
    migrate_db()
    
    logger.info("👁️ Глаз Исиды — OSINT запускается...")
    logger.info("🛡️ Канал: @Arhapov")
    logger.info("⚡ Таймаут поиска: 120 секунд")
    
    try:
        bot.remove_webhook()
        logger.info("✅ Webhook удалён")
    except Exception as e:
        logger.warning(f"⚠️ Ошибка удаления webhook: {e}")
    
    time.sleep(2)
    
    try:
        bot.infinity_polling(timeout=60, long_polling_timeout=30, skip_pending=True)
    except Exception as e:
        logger.error(f"❌ Ошибка polling: {e}")
        time.sleep(5)
        bot.infinity_polling(timeout=60, long_polling_timeout=30, skip_pending=True)
