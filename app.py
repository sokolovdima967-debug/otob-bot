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
from datetime import datetime
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

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "8545020464"))
DB_PATH = os.path.join("/tmp", "glaz_isidy_bot.db")
SEARCH_TIMEOUT = 120
TECH_MODE = False
user_state = {}

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

def safe_send_message(chat_id, text, parse_mode="Markdown", reply_markup=None, max_length=4000):
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
                email TEXT,
                fio TEXT,
                username_hide TEXT,
                ip TEXT,
                domain TEXT,
                status TEXT DEFAULT 'pending',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                reviewed_by INTEGER
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS hidden_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                contact_phone TEXT,
                phone TEXT,
                email TEXT,
                fio TEXT,
                username_hide TEXT,
                ip TEXT,
                domain TEXT,
                hidden_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id)
            )
        ''')
        conn.commit()
        conn.close()
        logger.info("✅ База данных инициализирована")
    except Exception as e:
        logger.error(f"❌ Ошибка БД: {e}")

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

bot = telebot.TeleBot(TOKEN, parse_mode="Markdown")
bot.remove_webhook()

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
    if re.search(r'^[a-zA-Z0-9_]{3,30}$', query):
        return "username"
    if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', query):
        return "ip"
    if "." in query and len(query.split()) == 1:
        return "domain"
    return "text"

def check_hidden_data(query: str) -> bool:
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute('''
            SELECT user_id, phone, email, fio, username_hide, ip, domain FROM hidden_data
        ''')
        hidden_rows = cur.fetchall()
        conn.close()
        if not hidden_rows:
            return False
        
        variants = []
        variants.append(query)
        variants.append(query.lower())
        variants.append(''.join(query.split()))
        variants.append(''.join(query.lower().split()))
        
        clean = re.sub(r'[\s\-()+]', '', query)
        clean = re.sub(r'^\+', '', clean)
        if clean.isdigit() and len(clean) >= 10:
            variants.append(clean)
            variants.append('+' + clean)
            variants.append('8' + clean[1:])
            variants.append('+8' + clean[1:])
            if clean.startswith('8'):
                variants.append('7' + clean[1:])
                variants.append('+7' + clean[1:])
            elif clean.startswith('7'):
                variants.append('8' + clean[1:])
                variants.append('+8' + clean[1:])
        
        if '@' in query:
            variants.append(query.lower())
            variants.append(query.replace('@', ''))
        
        if len(query.split()) >= 2:
            parts = query.split()
            for i in range(len(parts)):
                for j in range(len(parts)):
                    if i != j:
                        variants.append(' '.join([parts[i], parts[j]]))
            if len(parts) >= 2:
                variants.append(parts[0][0] + '. ' + parts[1])
        
        if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', query):
            variants.append(query.strip())
        
        variants = list(set(variants))
        
        for row in hidden_rows:
            owner_id, phone, email, fio, username_hide, ip, domain = row
            for v in variants:
                v = v.strip()
                if not v:
                    continue
                if phone:
                    phone_clean = re.sub(r'[\s\-()+]', '', phone)
                    phone_clean = re.sub(r'^\+', '', phone_clean)
                    phone_variants = [phone, phone_clean, '+' + phone_clean, '8' + phone_clean[1:], '+8' + phone_clean[1:], '7' + phone_clean[1:], '+7' + phone_clean[1:]]
                    if v in phone_variants:
                        _notify_owner(owner_id, query)
                        return True
                if email and (v == email.lower() or v == email.lower().replace('@', '')):
                    _notify_owner(owner_id, query)
                    return True
                if fio:
                    fio_clean = ' '.join(fio.lower().split())
                    v_clean = ' '.join(v.lower().split())
                    if v_clean == fio_clean:
                        _notify_owner(owner_id, query)
                        return True
                if username_hide and (v == username_hide.lower() or v == username_hide.lower().replace('@', '')):
                    _notify_owner(owner_id, query)
                    return True
                if ip and v == ip.strip():
                    _notify_owner(owner_id, query)
                    return True
                if domain and (v == domain.lower() or v == domain.lower().replace('www.', '') or v == 'www.' + domain.lower()):
                    _notify_owner(owner_id, query)
                    return True
        return False
    except Exception as e:
        logger.error(f"Check hidden error: {e}")
        return False

def _notify_owner(owner_id: int, query: str):
    try:
        bot.send_message(
            owner_id,
            f"🛡️ *Уведомление о попытке поиска*\n\n"
            f"🔍 Кто-то попытался найти информацию по запросу:\n"
            f"`{query}`\n\n"
            f"🛡️ *Ваши данные защищены!*\n"
            f"🔒 Информация не была передана.\n\n"
            f"👁️ @Arhapov",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Notify owner error: {e}")

# ==================== НОВЫЕ ФУНКЦИИ ПОИСКА ====================

async def telespotter_lookup(phone: str) -> dict:
    try:
        if os.path.exists("./telespotter/target/release/telespotter"):
            clean = clean_phone(phone)
            proc = await asyncio.create_subprocess_exec(
                "./telespotter/target/release/telespotter", clean, "-p", "-s",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            if stdout:
                data = json.loads(stdout)
                return {
                    "found": True,
                    "name": data.get('name', '—'),
                    "address": data.get('address', '—'),
                    "social": data.get('social_networks', []),
                    "relatives": data.get('relatives', [])
                }
    except:
        pass
    return await _telespotter_alternative(phone)

async def _telespotter_alternative(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        result = {"found": False, "name": "—", "address": "—"}
        url = f"https://www.whitepages.com/phone/{clean}"
        html = await parse_site_with_curl(url, {"result": ".person, .result, .card", "title": ".name, .fullname", "text": ".address, .location"})
        if html:
            for item in html:
                if item.get('title') and item.get('title') != '—':
                    result['name'] = item['title']
                    result['found'] = True
                if item.get('text') and item.get('text') != '—':
                    result['address'] = item['text']
                    result['found'] = True
        return result
    except:
        return {"found": False}

async def littlebrother_lookup(query: str) -> dict:
    try:
        clean = clean_phone(query)
        url = f"https://littlebrother.com/api/search?q={clean}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "found": True,
                        "name": data.get('name', '—'),
                        "address": data.get('address', '—'),
                        "social": data.get('social', [])
                    }
    except:
        pass
    return await _littlebrother_alternative(query)

async def _littlebrother_alternative(query: str) -> dict:
    try:
        clean = clean_phone(query)
        result = {"found": False}
        url = f"https://www.fastpeoplesearch.com/phone/{clean}"
        html = await parse_site_with_curl(url, {"result": ".result, .person-item", "title": ".name, .title", "text": ".description", "extra": ".address"})
        if html:
            for item in html:
                if item.get('title') and item.get('title') != '—':
                    result['name'] = item['title']
                    result['found'] = True
                if item.get('extra') and item.get('extra') != '—':
                    result['address'] = item['extra']
                    result['found'] = True
        return result
    except:
        return {"found": False}

async def olaosint_lookup(query: str) -> dict:
    try:
        clean = clean_phone(query)
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
    except:
        return {"found": False}

async def collector_lookup(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        parsed = phonenumbers.parse(f"+{clean}" if not clean.startswith('+') else clean, None)
        return {
            "found": True,
            "country": geocoder.country_name_for_number(parsed, "ru") or "—",
            "operator": carrier.name_for_number(parsed, "ru") or "—",
            "location": geocoder.description_for_number(parsed, "ru") or "—",
            "timezone": ", ".join(phone_timezone.time_zones_for_number(parsed)) or "—"
        }
    except:
        return {"found": False}

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

async def hunter_lookup(email: str) -> dict:
    if not HUNTER_KEY:
        return None
    url = f"https://api.hunter.io/v2/email-verifier?email={email}&api_key={HUNTER_KEY}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                result = data.get('data', {})
                return {
                    "found": True,
                    "status": result.get('status', '—'),
                    "score": result.get('score', 0),
                    "first_name": result.get('first_name', '—'),
                    "last_name": result.get('last_name', '—'),
                    "company": result.get('company', '—')
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

async def shodan_lookup(ip: str) -> dict:
    if not SHODAN_KEY:
        return None
    url = f"https://api.shodan.io/shodan/host/{ip}?key={SHODAN_KEY}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                return {
                    "found": True,
                    "country": data.get('country_name', '—'),
                    "city": data.get('city', '—'),
                    "org": data.get('org', '—'),
                    "isp": data.get('isp', '—'),
                    "ports": data.get('ports', [])[:5]
                }
    return {"found": False}

async def virustotal_lookup(domain: str) -> dict:
    if not VIRUSTOTAL_KEY:
        return None
    url = f"https://www.virustotal.com/api/v3/domains/{domain}"
    headers = {"x-apikey": VIRUSTOTAL_KEY}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                attributes = data.get('data', {}).get('attributes', {})
                return {
                    "found": True,
                    "reputation": attributes.get('reputation', 0),
                    "malicious": attributes.get('last_analysis_stats', {}).get('malicious', 0),
                    "suspicious": attributes.get('last_analysis_stats', {}).get('suspicious', 0)
                }
    return {"found": False}

async def emailrep_lookup(email: str) -> dict:
    url = f"https://emailrep.io/{email}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                return {
                    "found": True,
                    "reputation": data.get('reputation', '—'),
                    "suspicious": data.get('suspicious', False),
                    "references": data.get('references', 0)
                }
    return {"found": False}

async def hibp_lookup(email: str) -> list:
    url = f"https://haveibeenpwned.com/api/v3/breachedaccount/{email}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                return [b.get('Name') for b in data]
    return []

async def whitepages_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://www.whitepages.com/phone/{clean}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=8) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    if "No results found" not in html:
                        return {"found": True}
                return {"found": False}
    except:
        return {"found": False}

async def revealname_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://revealname.com/phone/{clean}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=8) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, 'html5lib')
                    name = soup.find('div', class_=re.compile('name|owner|result'))
                    if name:
                        return {"found": True, "name": name.get_text(strip=True)}
                return {"found": False}
    except:
        return {"found": False}

async def callerid_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://callerid.com/phone/{clean}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=8) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, 'html5lib')
                    name = soup.find('h1', class_=re.compile('name|title'))
                    if name:
                        return {"found": True, "name": name.get_text(strip=True)}
                return {"found": False}
    except:
        return {"found": False}

async def spydialer_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://www.spydialer.com/phone/{clean}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=8) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    if "No records found" not in html:
                        return {"found": True}
                return {"found": False}
    except:
        return {"found": False}

async def usphonebook_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://www.usphonebook.com/phone/{clean}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=8) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, 'html5lib')
                    name = soup.find('div', class_=re.compile('name|fullname'))
                    if name:
                        return {"found": True, "name": name.get_text(strip=True)}
                return {"found": False}
    except:
        return {"found": False}

async def fouroneone_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://www.411.com/phone/{clean}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=8) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, 'html5lib')
                    name = soup.find('div', class_=re.compile('name|result'))
                    if name:
                        return {"found": True, "name": name.get_text(strip=True)}
                return {"found": False}
    except:
        return {"found": False}

async def duckduckgo_search(query: str) -> list:
    url = f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}"
    selectors = {"result": ".result", "title": ".result__title a", "link": "a", "text": ".result__snippet"}
    return await parse_site_with_curl(url, selectors, 5)

async def socialsearch_lookup(query: str) -> list:
    url = f"https://socialsearch.io/?q={query.replace(' ', '+')}"
    selectors = {"result": ".result, .profile, .card", "title": ".name, .title", "link": "a", "text": ".description", "extra": ".url, .handle"}
    return await parse_site_with_curl(url, selectors, 5)

async def pipl_lookup(query: str) -> list:
    url = f"https://pipl.com/search/?q={query.replace(' ', '+')}"
    selectors = {"result": ".result, .person, .card", "title": ".name, .fullname", "link": "a", "text": ".bio, .description", "extra": ".location, .email, .phone, .social"}
    return await parse_site_with_curl(url, selectors, 5)

async def xray_lookup(query: str) -> list:
    url = f"https://x-ray.contact/search?q={query}"
    selectors = {"result": ".result-item, .social-link, .profile-item", "title": ".title, .name", "link": "a", "text": ".description", "extra": ".extra"}
    return await parse_site_with_curl(url, selectors, 5)

async def idcrawl_lookup(query: str) -> list:
    url = f"https://idcrawl.com/{query}"
    selectors = {"result": ".result-item, .profile-item", "title": ".title, .name", "link": "a", "text": ".description", "extra": ".extra"}
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
        f'site:avito.ru "{query}"',
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

async def dns_enum(domain: str) -> list:
    try:
        records = ['A', 'MX', 'NS', 'TXT', 'CNAME', 'SOA']
        results = []
        for record in records:
            try:
                answers = dns.resolver.resolve(domain, record)
                for rdata in answers:
                    results.append({"record": record, "value": str(rdata)})
            except:
                continue
        return results
    except:
        return []

async def ahmia_search(query: str) -> list:
    try:
        url = f"https://ahmia.fi/search/?q={query}"
        headers = {"User-Agent": ua.random}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, 'html5lib')
                    results = []
                    for item in soup.select('.result'):
                        title = item.select_one('h4 a')
                        desc = item.select_one('.description')
                        if title:
                            link = title.get('href')
                            if link and not link.startswith('http'):
                                link = f"https://ahmia.fi{link}"
                            results.append({
                                "title": title.get_text(strip=True)[:80] if title else "—",
                                "link": link or "—",
                                "text": desc.get_text(strip=True)[:200] if desc else "—"
                            })
                    return results
    except Exception as e:
        logger.error(f"Ahmia error: {e}")
    return []

async def exonera_check(ip: str) -> dict:
    try:
        url = f"https://exonerator.torproject.org/api/?ip={ip}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "found": True,
                        "is_tor": data.get('is_tor', False),
                        "first_seen": data.get('first_seen', '—'),
                        "last_seen": data.get('last_seen', '—')
                    }
    except Exception as e:
        logger.error(f"Exonera error: {e}")
    return {"found": False}

async def intelx_lookup(query: str) -> dict:
    if not INTELX_KEY:
        return None
    try:
        search_url = "https://2.intelx.io/phonebook/search"
        headers = {"x-key": INTELX_KEY}
        payload = {"term": query, "maxresults": 10, "media": 0}
        async with aiohttp.ClientSession() as session:
            async with session.post(search_url, headers=headers, json=payload, timeout=10) as resp:
                if resp.status != 200:
                    return None
                search_data = await resp.json()
                search_id = search_data.get('id')
                if not search_id:
                    return None
            await asyncio.sleep(2)
            result_url = f"https://2.intelx.io/phonebook/search/result?id={search_id}"
            async with session.get(result_url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('totalResults', 0) > 0:
                        return {
                            "found": True,
                            "total": data.get('totalResults', 0),
                            "results": data.get('selectors', [])[:5]
                        }
                return {"found": False}
    except Exception as e:
        logger.error(f"IntelX error: {e}")
        return {"found": False}

async def whatcms_lookup(domain: str) -> dict:
    if not WHATCMS_KEY:
        return None
    try:
        url = f"https://whatcms.org/APIEndpoint?key={WHATCMS_KEY}&url={domain}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('result', {}).get('code') == 200:
                        result = data.get('result', {})
                        return {
                            "found": True,
                            "cms": result.get('name', '—'),
                            "version": result.get('version', '—'),
                            "cms_url": result.get('cms_url', '—'),
                            "confidence": result.get('confidence', '—')
                        }
                return {"found": False}
    except Exception as e:
        logger.error(f"WhatCMS error: {e}")
        return {"found": False}

# ==================== ПАРСЕР С ОБХОДОМ ====================

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

# ==================== ГЛОБАЛЬНЫЙ ПОИСК ====================

async def global_lookup(query: str) -> dict:
    query = query.strip()
    qtype = detect_query_type(query)
    
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
            ("olaosint", run_with_timeout(olaosint_lookup(query), 8)),
            ("collector", run_with_timeout(collector_lookup(query), 8)),
            ("telespotter", run_with_timeout(telespotter_lookup(query), 15)),
            ("littlebrother", run_with_timeout(littlebrother_lookup(query), 15)),
            ("numverify", run_with_timeout(numverify_lookup(query), 8)),
            ("abstractapi", run_with_timeout(abstractapi_lookup(query), 8)),
            ("leakcheck", run_with_timeout(leakcheck_lookup(query), 8)),
            ("hudsonrock", run_with_timeout(hudsonrock_lookup(query), 8)),
            ("whitepages", run_with_timeout(whitepages_check(query), 8)),
            ("revealname", run_with_timeout(revealname_check(query), 8)),
            ("callerid", run_with_timeout(callerid_check(query), 8)),
            ("spydialer", run_with_timeout(spydialer_check(query), 8)),
            ("usphonebook", run_with_timeout(usphonebook_check(query), 8)),
            ("fouroneone", run_with_timeout(fouroneone_check(query), 8)),
            ("duckduckgo", run_with_timeout(duckduckgo_search(query), 10)),
            ("socialsearch", run_with_timeout(socialsearch_lookup(query), 8)),
            ("pipl", run_with_timeout(pipl_lookup(query), 8)),
            ("xray", run_with_timeout(xray_lookup(query), 10)),
            ("idcrawl", run_with_timeout(idcrawl_lookup(query), 8)),
            ("google_dorks", run_with_timeout(google_dorks_search(query), 10)),
            ("ahmia", run_with_timeout(ahmia_search(query), 12)),
            ("intelx", run_with_timeout(intelx_lookup(query), 10)),
        ]
    
    elif qtype == "email":
        tasks = [
            ("hunter", run_with_timeout(hunter_lookup(query), 8)),
            ("emailrep", run_with_timeout(emailrep_lookup(query), 8)),
            ("hibp", run_with_timeout(hibp_lookup(query), 8)),
            ("leakcheck", run_with_timeout(leakcheck_lookup(query), 8)),
            ("socialsearch", run_with_timeout(socialsearch_lookup(query), 8)),
            ("pipl", run_with_timeout(pipl_lookup(query), 8)),
            ("duckduckgo", run_with_timeout(duckduckgo_search(query), 10)),
            ("ahmia", run_with_timeout(ahmia_search(query), 12)),
            ("intelx", run_with_timeout(intelx_lookup(query), 10)),
        ]
    
    elif qtype == "username":
        tasks = [
            ("xray", run_with_timeout(xray_lookup(query), 10)),
            ("idcrawl", run_with_timeout(idcrawl_lookup(query), 8)),
            ("socialsearch", run_with_timeout(socialsearch_lookup(query), 8)),
            ("pipl", run_with_timeout(pipl_lookup(query), 8)),
            ("duckduckgo", run_with_timeout(duckduckgo_search(query), 10)),
            ("leakcheck", run_with_timeout(leakcheck_lookup(query), 8)),
            ("ahmia", run_with_timeout(ahmia_search(query), 12)),
            ("intelx", run_with_timeout(intelx_lookup(query), 10)),
        ]
    
    elif qtype == "ip":
        tasks = [
            ("ipinfo", run_with_timeout(ipinfo_lookup(query), 6)),
            ("ip_api", run_with_timeout(ip_api_lookup(query), 6)),
            ("shodan", run_with_timeout(shodan_lookup(query), 8)),
            ("exonera", run_with_timeout(exonera_check(query), 8)),
            ("duckduckgo", run_with_timeout(duckduckgo_search(query), 10)),
        ]
    
    elif qtype == "domain":
        tasks = [
            ("virustotal", run_with_timeout(virustotal_lookup(query), 8)),
            ("dns_enum", run_with_timeout(dns_enum(query), 8)),
            ("whatcms", run_with_timeout(whatcms_lookup(query), 8)),
            ("duckduckgo", run_with_timeout(duckduckgo_search(query), 10)),
            ("ahmia", run_with_timeout(ahmia_search(query), 12)),
        ]
    else:
        tasks = [
            ("duckduckgo", run_with_timeout(duckduckgo_search(query), 10)),
            ("ahmia", run_with_timeout(ahmia_search(query), 12)),
            ("intelx", run_with_timeout(intelx_lookup(query), 10)),
        ]
    
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

# ==================== ГЕНЕРАЦИЯ HTML-ОТЧЁТА ====================

def generate_html_report(query: str, data: dict, report_id: str) -> str:
    sources = data.get("sources", {})
    qtype = data.get("type", "text")
    total = data.get("total_results", 0)
    
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
                <div class="sub" style="color:#4a2a2a; margin-top:2px;">🛡️ 60+ источников · Глубокий OSINT · Даркнет</div>
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
            
            is_darknet = source in ['ahmia', 'darksearch', 'exonera', 'intelx']
            tag_class = 'darknet-tag' if is_darknet else 'source-tag'
            tag_icon = '🧅 ' if is_darknet else ''
            
            html += f"""
        <div class="result-item">
            <div class="title">
                <span class="index">#{idx}</span>
                {title}
                <span class="{tag_class}">{tag_icon}{source[:12]}</span>
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
            <span style="margin-left:15px; color:#aa44aa;">🧅 Даркнет-источники помечены</span>
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
    bot.answer_callback_query(call.id)
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("📱 Отправить контакт", callback_data="send_contact_hide"),
        types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back")
    )
    
    bot.edit_message_text(
        "🔒 *Скрытие данных*\n\n"
        "📌 *Для подачи заявки нажми кнопку ниже и отправь свой контакт.*\n\n"
        "Это нужно для идентификации твоего аккаунта.\n\n"
        "После этого заполни данные для скрытия.",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="Markdown",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data == "send_contact_hide")
def send_contact_hide(call):
    user_id = call.from_user.id
    bot.answer_callback_query(call.id)
    
    markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True, one_time_keyboard=True)
    contact_button = types.KeyboardButton("📱 Отправить контакт", request_contact=True)
    markup.add(contact_button)
    
    bot.send_message(
        user_id,
        "📱 *Нажми кнопку ниже, чтобы отправить свой контакт.*\n\n"
        "Это нужно для подтверждения твоей личности.",
        parse_mode="Markdown",
        reply_markup=markup
    )
    user_state[user_id] = "awaiting_contact_for_hide"

@bot.message_handler(content_types=['contact'])
def handle_contact(message):
    user_id = message.from_user.id
    
    if user_state.get(user_id) == "awaiting_contact_for_hide":
        contact = message.contact
        user_state.pop(user_id, None)
        
        user_state[f"hide_contact_{user_id}"] = {
            "phone": contact.phone_number,
            "user_id": contact.user_id,
            "first_name": contact.first_name,
            "last_name": contact.last_name,
            "username": message.from_user.username
        }
        
        markup = types.ReplyKeyboardRemove()
        bot.send_message(
            user_id,
            "✅ *Контакт получен!*\n\n"
            "📝 Теперь отправь данные для скрытия в формате:\n\n"
            "`ФИО: Иванов Иван Иванович`\n"
            "`ПОЧТА: user@example.com`\n"
            "`ТЕЛЕФОН: +79991234567`\n"
            "`USERNAME: username`\n"
            "`IP: 8.8.8.8`\n"
            "`ДОМЕН: example.com`\n\n"
            "📌 Можно отправить не все поля, только то, что хочешь скрыть.",
            parse_mode="Markdown",
            reply_markup=markup
        )
        user_state[user_id] = "awaiting_hide_data_fields"

@bot.message_handler(func=lambda message: user_state.get(message.from_user.id) == "awaiting_hide_data_fields")
def process_hide_data_fields(message):
    user_id = message.from_user.id
    text = message.text.strip()
    user_state.pop(user_id, None)
    
    data = {
        "phone": None,
        "email": None,
        "fio": None,
        "username_hide": None,
        "ip": None,
        "domain": None
    }
    
    lines = text.split('\n')
    for line in lines:
        line = line.strip()
        if ':' in line:
            key, value = line.split(':', 1)
            key = key.strip().upper()
            value = value.strip()
            if key == 'ФИО':
                data["fio"] = value
            elif key == 'ПОЧТА':
                data["email"] = value
            elif key == 'ТЕЛЕФОН':
                data["phone"] = value
            elif key == 'USERNAME':
                data["username_hide"] = value
            elif key == 'IP':
                data["ip"] = value
            elif key == 'ДОМЕН':
                data["domain"] = value
    
    contact = user_state.get(f"hide_contact_{user_id}", {})
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO hide_requests (
                user_id, username, contact_phone, phone, email, fio, username_hide, ip, domain, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        ''', (
            user_id,
            contact.get('username') or message.from_user.username or 'нет',
            contact.get('phone') or '—',
            data["phone"], data["email"], data["fio"],
            data["username_hide"], data["ip"], data["domain"]
        ))
        request_id = cur.lastrowid
        conn.commit()
        conn.close()
        
        admin_text = (
            f"🔔 *Новая заявка на скрытие данных # {request_id}*\n\n"
            f"👤 Пользователь: @{contact.get('username') or 'нет'} | `{user_id}`\n"
            f"📱 Контактный телефон: {contact.get('phone') or '—'}\n"
            f"📱 Телефон: {data['phone'] or '—'}\n"
            f"📧 Почта: {data['email'] or '—'}\n"
            f"👤 ФИО: {data['fio'] or '—'}\n"
            f"🆔 Username: {data['username_hide'] or '—'}\n"
            f"🌐 IP: {data['ip'] or '—'}\n"
            f"🌍 Домен: {data['domain'] or '—'}"
        )
        
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_hide_{request_id}"),
            types.InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_hide_{request_id}")
        )
        bot.send_message(ADMIN_ID, admin_text, parse_mode="Markdown", reply_markup=markup)
        
        bot.send_message(
            user_id,
            "✅ *Заявка отправлена!*\n\n"
            "⏳ Ожидай решения администратора.\n"
            "📨 Уведомление придёт, когда заявка будет рассмотрена.",
            parse_mode="Markdown"
        )
    except Exception as e:
        bot.send_message(user_id, f"⚠️ Ошибка: {str(e)[:100]}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('approve_hide_'))
def approve_hide(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌ Только для админа!", show_alert=True)
        return
    
    request_id = int(call.data.split('_')[2])
    bot.answer_callback_query(call.id, "✅ Заявка одобрена!")
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute('''
            SELECT user_id, username, contact_phone, phone, email, fio, username_hide, ip, domain 
            FROM hide_requests WHERE id = ?
        ''', (request_id,))
        row = cur.fetchone()
        if not row:
            bot.send_message(ADMIN_ID, "❌ Заявка не найдена.")
            return
        
        user_id, username, contact_phone, phone, email, fio, username_hide, ip, domain = row
        
        cur.execute('''
            INSERT OR REPLACE INTO hidden_data (
                user_id, username, contact_phone, phone, email, fio, username_hide, ip, domain
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, username, contact_phone, phone, email, fio, username_hide, ip, domain))
        
        cur.execute("UPDATE hide_requests SET status = 'approved', reviewed_by = ? WHERE id = ?", (ADMIN_ID, request_id))
        conn.commit()
        conn.close()
        
        bot.send_message(
            user_id,
            "✅ *Ваша заявка на скрытие данных ОДОБРЕНА!*\n\n"
            "🔒 Ваши данные теперь скрыты от поиска.\n\n"
            "📋 Скрытые данные:\n"
            f"• Телефон: {phone or '—'}\n"
            f"• Email: {email or '—'}\n"
            f"• ФИО: {fio or '—'}\n"
            f"• Username: {username_hide or '—'}\n"
            f"• IP: {ip or '—'}\n"
            f"• Домен: {domain or '—'}",
            parse_mode="Markdown"
        )
        bot.send_message(ADMIN_ID, f"✅ Заявка #{request_id} одобрена.")
    except Exception as e:
        bot.send_message(ADMIN_ID, f"⚠️ Ошибка: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('reject_hide_'))
def reject_hide(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌ Только для админа!", show_alert=True)
        return
    
    request_id = int(call.data.split('_')[2])
    bot.answer_callback_query(call.id, "❌ Заявка отклонена!")
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM hide_requests WHERE id = ?", (request_id,))
        row = cur.fetchone()
        if row:
            user_id = row[0]
            cur.execute("UPDATE hide_requests SET status = 'rejected', reviewed_by = ? WHERE id = ?", (ADMIN_ID, request_id))
            conn.commit()
            bot.send_message(
                user_id,
                "❌ *Ваша заявка на скрытие данных ОТКЛОНЕНА*\n\n"
                "📌 Возможно, данные не соответствуют требованиям.\n"
                "🔄 Попробуй отправить заявку ещё раз с корректными данными.",
                parse_mode="Markdown"
            )
        conn.close()
        bot.send_message(ADMIN_ID, f"❌ Заявка #{request_id} отклонена.")
    except Exception as e:
        bot.send_message(ADMIN_ID, f"⚠️ Ошибка: {e}")

@bot.callback_query_handler(func=lambda call: call.data == "list_hide_requests")
def list_hide_requests(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌ Только для админа!", show_alert=True)
        return
    
    bot.answer_callback_query(call.id)
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT id, user_id, username, phone, email, fio, created_at FROM hide_requests WHERE status = 'pending' ORDER BY created_at DESC")
        rows = cur.fetchall()
        conn.close()
        
        if not rows:
            bot.send_message(ADMIN_ID, "📋 *Нет активных заявок.*", parse_mode="Markdown")
            return
        
        text = "📋 *Заявки на скрытие данных*\n\n"
        for row in rows[:10]:
            req_id, user_id, username, phone, email, fio, created = row
            text += (
                f"🔹 *#{req_id}* | @{username or 'нет'} | `{user_id}`\n"
                f"   📱 {phone or '—'} | 📧 {email or '—'}\n"
                f"   👤 {fio or '—'}\n"
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
        
        bot.send_message(ADMIN_ID, text, parse_mode="Markdown", reply_markup=markup)
    except Exception as e:
        bot.send_message(ADMIN_ID, f"⚠️ Ошибка: {e}")

# ==================== АДМИН-КОМАНДЫ ====================

@bot.message_handler(commands=['adminhelp'])
def admin_help_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Только для админа.")
        return
    
    help_text = (
        "👑 *Админ-команды Глаз Исиды*\n\n"
        "📌 *Управление пользователями*\n"
        "`/give <кол-во> <user_id>` — выдать запросы\n"
        "`/take <кол-во> <user_id>` — забрать запросы\n"
        "`/users` — список пользователей\n"
        "`/hide <user_id>` — раскрыть скрытые данные\n\n"
        "📌 *Управление ботом*\n"
        "`/tech on/off` — техперерыв\n"
        "`/stats` — статистика бота\n"
        "`/broadcast <текст>` — рассылка\n\n"
        "📌 *Скрытие данных*\n"
        "`/requests` — список заявок на скрытие\n"
        "`/hide <user_id>` — раскрыть данные\n\n"
        "👁️ *Глаз Исиды — OSINT*\n"
        "🛡️ @Arhapov"
    )
    bot.reply_to(message, help_text, parse_mode="Markdown")

@bot.message_handler(commands=['tech'])
def tech_command(message):
    global TECH_MODE
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Только для админа.")
        return
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "❗ /tech on/off")
        return
    if args[1].lower() == "on":
        TECH_MODE = True
        bot.reply_to(message, "🔧 *Техперерыв ВКЛЮЧЁН*", parse_mode="Markdown")
    elif args[1].lower() == "off":
        TECH_MODE = False
        bot.reply_to(message, "✅ *Техперерыв ВЫКЛЮЧЕН*", parse_mode="Markdown")
    else:
        bot.reply_to(message, "❗ /tech on или /tech off")

@bot.message_handler(commands=['stats'])
def stats_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Только для админа.")
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
        bot.reply_to(
            message,
            f"📊 *Статистика бота*\n\n"
            f"👥 Всего пользователей: **{total_users}**\n"
            f"🔍 Всего поисков: **{total_searches}**\n"
            f"📋 Заявок на скрытие: **{pending_requests}**\n"
            f"👑 Админ: @Arhapov",
            parse_mode="Markdown"
        )
    except Exception as e:
        bot.reply_to(message, f"⚠️ Ошибка: {e}")

@bot.message_handler(commands=['broadcast'])
def broadcast_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Только для админа.")
        return
    text = message.text.replace('/broadcast', '').strip()
    if not text:
        bot.reply_to(message, "❗ /broadcast <текст сообщения>")
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
                bot.send_message(user[0], f"📢 *Рассылка*\n\n{text}", parse_mode="Markdown")
                sent += 1
                time.sleep(0.05)
            except:
                continue
        bot.reply_to(message, f"✅ Рассылка отправлена **{sent}** пользователям.", parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"⚠️ Ошибка: {e}")

@bot.message_handler(commands=['requests'])
def requests_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Только для админа.")
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT id, user_id, username, phone, email, fio, created_at FROM hide_requests WHERE status = 'pending' ORDER BY created_at DESC")
        rows = cur.fetchall()
        conn.close()
        if not rows:
            bot.reply_to(message, "📋 *Нет активных заявок.*", parse_mode="Markdown")
            return
        text = "📋 *Заявки на скрытие данных*\n\n"
        for row in rows[:10]:
            req_id, user_id, username, phone, email, fio, created = row
            text += (
                f"🔹 *#{req_id}* | @{username or 'нет'} | `{user_id}`\n"
                f"   📱 {phone or '—'} | 📧 {email or '—'}\n"
                f"   👤 {fio or '—'}\n"
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
        bot.reply_to(message, text, parse_mode="Markdown", reply_markup=markup)
    except Exception as e:
        bot.reply_to(message, f"⚠️ Ошибка: {e}")

@bot.message_handler(commands=['hide'])
def hide_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Только для админа.")
        return
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "❗ /hide <user_id>")
        return
    target_id = int(args[1])
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("DELETE FROM hidden_data WHERE user_id = ?", (target_id,))
        conn.commit()
        conn.close()
        bot.reply_to(
            message,
            f"✅ Данные пользователя `{target_id}` раскрыты.\n\n🔓 Теперь его данные снова видны в поиске.",
            parse_mode="Markdown"
        )
    except Exception as e:
        bot.reply_to(message, f"⚠️ Ошибка: {e}")

@bot.message_handler(commands=['give'])
def give_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Только для админа.")
        return
    try:
        args = message.text.split()
        if len(args) < 3:
            bot.reply_to(message, "❗ /give <кол-во> <user_id>")
            return
        amount = int(args[1])
        target_id = int(args[2])
        user = get_user(target_id)
        user["searches_today"] = max(0, user["searches_today"] - amount)
        update_user(target_id, user)
        bot.reply_to(message, f"✅ Выдано {amount} запросов пользователю `{target_id}`.", parse_mode="Markdown")
    except:
        bot.reply_to(message, "⚠️ Ошибка. /give <кол-во> <user_id>")

@bot.message_handler(commands=['take'])
def take_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Только для админа.")
        return
    try:
        args = message.text.split()
        if len(args) < 3:
            bot.reply_to(message, "❗ /take <кол-во> <user_id>")
            return
        amount = int(args[1])
        target_id = int(args[2])
        user = get_user(target_id)
        user["searches_extra"] = max(0, user["searches_extra"] - amount)
        update_user(target_id, user)
        bot.reply_to(message, f"✅ Забрано {amount} запросов у пользователя `{target_id}`.", parse_mode="Markdown")
    except:
        bot.reply_to(message, "⚠️ Ошибка. /take <кол-во> <user_id>")

@bot.message_handler(commands=['users'])
def users_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Только для админа.")
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT user_id, username, searches_today, searches_extra FROM users ORDER BY searches_today DESC")
        rows = cur.fetchall()
        conn.close()
        if not rows:
            bot.reply_to(message, "📊 Нет пользователей.")
            return
        text = "📊 *Список пользователей*\n\n"
        for user_id, username, today, extra in rows[:20]:
            total = (5 - today) + extra
            text += f"• `{user_id}` — @{username or 'нет'} | запросов: {total}\n"
        bot.reply_to(message, text, parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"⚠️ Ошибка: {e}")

# ==================== МЕНЮ ====================

def main_menu_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("👁️ ГЛОБАЛЬНЫЙ ПОИСК", callback_data="global_search")
    )
    markup.add(
        types.InlineKeyboardButton("📱 ТЕЛЕФОН", callback_data="phone_search"),
        types.InlineKeyboardButton("📧 EMAIL", callback_data="email_search")
    )
    markup.add(
        types.InlineKeyboardButton("👤 USERNAME", callback_data="username_search"),
        types.InlineKeyboardButton("🌐 IP/ДОМЕН", callback_data="domain_search")
    )
    markup.add(
        types.InlineKeyboardButton("🌍 ГЕОИНТ", callback_data="geoint_search"),
        types.InlineKeyboardButton("🖼️ МЕТАДАННЫЕ", callback_data="metadata_search")
    )
    markup.add(
        types.InlineKeyboardButton("📱 TELEGRAM", callback_data="telegram_search")
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

# ==================== ОБРАБОТЧИКИ ====================

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    try:
        bot.answer_callback_query(call.id)
        
        if call.data == "menu_back":
            bot.edit_message_text(
                "👁️ *Глаз Исиды — OSINT*\n\n"
                "🕵️ *Глубокий OSINT-поиск*\n"
                "60+ источников\n\n"
                "⚡ *Выбери действие:*",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=main_menu_keyboard()
            )
            return
        
        if call.data == "menu_profile":
            user = call.from_user
            user_data = get_user(user.id, user.username or "Unknown")
            remaining = get_remaining(user.id)
            text = (
                "👤 *Твой профиль*\n\n"
                f"🆔 ID: `{user.id}`\n"
                f"👤 Username: @{user.username or 'нет'}\n"
                f"📛 Имя: {user.first_name or '—'}\n\n"
                f"📊 Использовано: {user_data['searches_today']}/5\n"
                f"📊 Бонусных: {user_data['searches_extra']}\n"
                f"📊 Осталось: {remaining}\n"
                f"⏰ Сброс: в 00:00 МСК\n"
                f"👑 Админ: {'✅' if user.id == ADMIN_ID else '❌'}"
            )
            bot.edit_message_text(
                text,
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back")
                )
            )
            return
        
        if call.data == "menu_balance":
            user_id = call.from_user.id
            remaining = get_remaining(user_id)
            used = get_user(user_id)["searches_today"]
            extra = get_user(user_id)["searches_extra"]
            text = (
                "📊 *Твой баланс*\n\n"
                f"🔍 Использовано: {used}/5\n"
                f"📊 Бонусных: {extra}\n"
                f"📊 Осталось: {remaining}\n"
                f"⏰ Сброс: в 00:00 МСК\n"
                f"👑 Админ: {'♾️ безлимитный' if user_id == ADMIN_ID else 'нет'}"
            )
            bot.edit_message_text(
                text,
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back")
                )
            )
            return
        
        if call.data == "menu_help":
            bot.edit_message_text(
                "❓ *Помощь*\n\n"
                "📌 *Как пользоваться:*\n"
                "• Отправь номер, email, никнейм, IP или домен\n\n"
                "🧅 *Даркнет:* Ahmia · Exonera Tor · IntelX\n\n"
                "📊 *Лимит:* 5 поисков в день\n"
                "👑 *Админ:* безлимитный доступ\n"
                "🔒 *Скрытие данных:* отправь заявку админу\n\n"
                "🛡️ @Arhapov",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back")
                )
            )
            return
        
        if call.data == "global_search":
            bot.edit_message_text(
                "🌐 *ГЛОБАЛЬНЫЙ ПОИСК*\n\n"
                "Отправь запрос для поиска:\n"
                "• 📱 Номер: `+79991234567`\n"
                "• 👤 ФИО: `Иванов Иван Иванович`\n"
                "• 📧 Email: `user@example.com`\n"
                "• 🆔 Никнейм: `username`\n"
                "• 🌐 IP: `8.8.8.8`\n"
                "• 🌍 Домен: `example.com`\n\n"
                "⚡ *60+ OSINT-источников*\n"
                "🧅 Даркнет: Ahmia · Exonera Tor · IntelX",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back")
                )
            )
            return
        
        if call.data == "phone_search":
            bot.edit_message_text(
                "📱 *ПРОВЕРКА ТЕЛЕФОНА*\n\n"
                "Отправь номер для проверки.\n\n"
                "Пример: `+79991234567`\n\n"
                "🔍 ФИО · Адреса · Утечки · Соцсети",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back")
                )
            )
            return
        
        if call.data == "email_search":
            bot.edit_message_text(
                "📧 *ПРОВЕРКА EMAIL*\n\n"
                "Отправь email для проверки.\n\n"
                "Пример: `user@example.com`\n\n"
                "🔍 Утечки · Репутация · Компания",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back")
                )
            )
            return
        
        if call.data == "username_search":
            bot.edit_message_text(
                "👤 *ПОИСК ПО USERNAME*\n\n"
                "Отправь никнейм для поиска.\n\n"
                "Пример: `username`\n\n"
                "🔍 GitHub · Telegram · Соцсети · Утечки",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back")
                )
            )
            return
        
        if call.data == "domain_search":
            bot.edit_message_text(
                "🌐 *ПОИСК ПО IP/ДОМЕНУ*\n\n"
                "Отправь IP или домен.\n\n"
                "Пример IP: `8.8.8.8`\n"
                "Пример домена: `example.com`\n\n"
                "🔍 Геолокация · WHOIS · SSL · DNS · CMS",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back")
                )
            )
            return
        
        if call.data == "geoint_search":
            bot.edit_message_text(
                "🌍 *ГЕОИНТ*\n\n"
                "Отправь координаты или адрес.\n\n"
                "Пример координат: `55.7558,37.6173`\n"
                "Пример адреса: `Москва, Кремль`",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back")
                )
            )
            return
        
        if call.data == "metadata_search":
            bot.edit_message_text(
                "🖼️ *МЕТАДАННЫЕ ФОТО*\n\n"
                "Отправь ссылку на фото для извлечения EXIF-данных.\n\n"
                "Пример: `https://example.com/photo.jpg`",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back")
                )
            )
            return
        
        if call.data == "telegram_search":
            bot.edit_message_text(
                "📱 *ПОИСК ПО TELEGRAM*\n\n"
                "Отправь username Telegram аккаунта.\n\n"
                "Пример: `@durov`",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back")
                )
            )
            return
        
        if call.data == "hide_data":
            hide_data_callback(call)
            return
            
    except Exception as e:
        logger.error(f"❌ Callback error: {e}")

# ==================== ОСНОВНЫЕ КОМАНДЫ ====================

@bot.message_handler(commands=['start'])
def start_command(message):
    try:
        remaining = get_remaining(message.from_user.id)
        bot.send_message(
            message.chat.id,
            f"👁️ *Глаз Исиды — OSINT*\n\n"
            f"🕵️ Привет, {message.from_user.first_name}!\n"
            f"⚡ Глубокий OSINT-поиск\n"
            f"🛡️ 60+ источников\n"
            f"🧅 Даркнет: Ahmia · Exonera · IntelX\n\n"
            f"📊 *Осталось:* {remaining}/5\n\n"
            f"📌 *Выбери действие:*",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard()
        )
    except Exception as e:
        logger.error(f"Start error: {e}")

# ==================== ОБРАБОТЧИК ТЕКСТА ====================

@bot.message_handler(func=lambda message: True)
def handle_text(message):
    try:
        text = message.text.strip()
        if not text or text.startswith('/'):
            return
        
        if TECH_MODE and message.from_user.id != ADMIN_ID:
            safe_send_message(
                message.chat.id,
                "🔧 *Бот на техническом обслуживании*\n\n⏰ Вернёмся через несколько минут!"
            )
            return
        
        user_id = message.from_user.id
        chat_id = message.chat.id
        
        if not can_search(user_id):
            safe_send_message(
                chat_id,
                "❌ *Лимит поисков исчерпан!*\n\n⏰ Сброс в 00:00 МСК"
            )
            return
        
        # ====== ПРОВЕРКА СКРЫТЫХ ДАННЫХ ======
        if check_hidden_data(text):
            safe_send_message(
                chat_id,
                "🔒 *Человек скрыл свои данные*\n\n"
                "Данные этого пользователя скрыты по его запросу.\n\n"
                "🛡️ @Arhapov"
            )
            return
        # =====================================
        
        start_time = time.time()
        msg = safe_send_message(
            chat_id,
            "👁️ *Глаз Исиды — глубокое сканирование...*\n"
            "⏱️ Время: до 120 секунд\n"
            "🕵️ 60+ источников...\n"
            "🧅 Поиск в даркнете..."
        )
        
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                data = loop.run_until_complete(
                    asyncio.wait_for(global_lookup(text), timeout=120)
                )
            except asyncio.TimeoutError:
                bot.edit_message_text(
                    "⚠️ *Поиск прерван по таймауту (120 секунд)*\n\n"
                    "📌 Показаны только быстрые результаты.",
                    chat_id,
                    msg.message_id,
                    parse_mode="Markdown"
                )
                data = {"query": text, "type": "unknown", "sources": {}, "total_results": 0}
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"❌ Ошибка выполнения поиска: {e}")
            bot.edit_message_text(
                f"⚠️ Ошибка: {str(e)[:100]}",
                chat_id,
                msg.message_id
            )
            return
        
        elapsed = time.time() - start_time
        total = data.get("total_results", 0)
        remaining = use_search(user_id)
        
        report_id = f"{user_id}_{int(datetime.now().timestamp())}"
        html = generate_html_report(text, data, report_id)
        
        reports[report_id] = {"query": text, "data": data, "html": html, "created": datetime.now().timestamp()}
        
        filename = f"{user_id}_{int(datetime.now().timestamp())}.html"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(html)
        
        with open(filename, "rb") as f:
            caption = (
                f"👁️ *OSINT-ОТЧЁТ*\n\n"
                f"🔍 Запрос: `{text}`\n"
                f"📌 Найдено: **{total}**\n"
                f"🔍 Осталось: **{remaining}/5**\n"
                f"⏱️ Время: **{elapsed:.1f} сек**\n"
                f"🧅 Даркнет: Ahmia · Exonera Tor · IntelX\n"
                f"🛡️ @Arhapov"
            )
            if len(caption) > 1000:
                caption = caption[:997] + "..."
            
            bot.send_document(
                chat_id,
                f,
                caption=caption,
                parse_mode="Markdown"
            )
        
        os.remove(filename)
        bot.delete_message(chat_id, msg.message_id)
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        try:
            bot.send_message(
                message.chat.id,
                f"⚠️ Ошибка: {str(e)[:100]}"
            )
        except:
            pass

# ==================== ЗАПУСК ====================

if __name__ == "__main__":
    init_db()
    logger.info("👁️ Глаз Исиды — OSINT запускается...")
    logger.info("🛡️ Канал: @Arhapov")
    logger.info("⚡ Таймаут поиска: 120 секунд")
    logger.info("📊 60+ источников")
    logger.info("🧅 Даркнет: Ahmia · Exonera Tor · IntelX · WhatCMS")
    logger.info("🔄 Обход блокировок: curl_cffi")
    logger.info("🔒 Скрытие данных: бесплатно (заявка админу)")
    logger.info("👁️ С поддержкой Архапова")
    
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
