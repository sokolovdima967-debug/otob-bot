import os
import time
import logging
import re
import sqlite3
import random
import aiohttp
import asyncio
import json
import subprocess
import sys
import csv
import io
import threading
import traceback
from datetime import datetime
from bs4 import BeautifulSoup
import telebot
from telebot import types
from http.server import HTTPServer, BaseHTTPRequestHandler

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "8545020464"))
DB_PATH = os.path.join("/tmp", "otob_bot.db")

# ===== КЛЮЧИ API =====
VERIPHONE_KEY = os.environ.get("VERIPHONE_KEY")
OMKAR_KEY = os.environ.get("OMKAR_KEY")
NUMVERIFY_KEY = os.environ.get("NUMVERIFY_KEY")
ABSTRACT_API_KEY = os.environ.get("ABSTRACT_API_KEY")
BIGDATACLOUD_KEY = os.environ.get("BIGDATACLOUD_KEY")
HUNTER_KEY = os.environ.get("HUNTER_KEY")
SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY")
APILAYER_KEY = os.environ.get("APILAYER_KEY")
FULLCONTACT_KEY = os.environ.get("FULLCONTACT_KEY")

if not TOKEN:
    raise ValueError("❌ TOKEN не установлен!")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== БАЗА ДАННЫХ ====================
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
        return user["searches_today"] < 3 or user["searches_extra"] > 0
    except:
        return False

def use_search(user_id: int) -> int:
    if user_id == ADMIN_ID:
        return 999
    try:
        reset_daily_searches()
        user = get_user(user_id)
        if user["searches_today"] < 3:
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
        return (3 - user["searches_today"]) + user["searches_extra"]
    except:
        return 0

# ==================== ХРАНИЛИЩЕ ОТЧЁТОВ ====================
reports = {}

# ==================== ИНИЦИАЛИЗАЦИЯ БОТА ====================
bot = telebot.TeleBot(TOKEN, parse_mode="Markdown")
bot.remove_webhook()

# ==================== HTTP-СЕРВЕР ====================

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

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

def generate_otob_title(query: str, qtype: str) -> str:
    templates = [
        f"🔍 OTOB — OSINT Глобальный поиск | {qtype.upper()} | {query}",
        f"🕵️ OTOB | {query} | {qtype.upper()} | Отчёт разведки",
        f"🎯 OTOB — Sherlock OSINT | {qtype} | {query}",
        f"⚡ OTOB — Глаз Бога | {query} | {qtype.upper()}",
        f"🔱 OTOB — Leak OSINT | {query} | {qtype.upper()}",
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

def request_via_scraperapi(url: str) -> str:
    if not SCRAPERAPI_KEY:
        return None
    try:
        import requests
        proxy_url = f"https://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={url}"
        response = requests.get(proxy_url, timeout=30)
        if response.status_code == 200:
            return response.text
    except:
        pass
    return None

# ==================== ОПРЕДЕЛЕНИЕ ТИПА ЗАПРОСА ====================
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

# ==================== ЛЕГАЛЬНЫЕ API ====================

@safe_request
async def numverify_lookup(phone: str) -> dict:
    if not NUMVERIFY_KEY:
        return None
    clean = re.sub(r'\D', '', phone)
    url = f"https://api.numverify.com/validate?access_key={NUMVERIFY_KEY}&number={clean}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=30) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get('valid'):
                    return {
                        "country": data.get('country_name', '—'),
                        "location": data.get('location', '—'),
                        "carrier": data.get('carrier', '—'),
                        "line_type": data.get('line_type', '—')
                    }
    return None

@safe_request
async def veriphone_lookup(phone: str) -> dict:
    if not VERIPHONE_KEY:
        return None
    clean = re.sub(r'\D', '', phone)
    url = f"https://api.veriphone.io/v2/verify?phone=%2B{clean}&key={VERIPHONE_KEY}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=30) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get('phone_valid'):
                    return {
                        "country": data.get('country', '—'),
                        "carrier": data.get('carrier', '—'),
                        "type": data.get('phone_type', '—')
                    }
    return None

@safe_request
async def abstractapi_lookup(phone: str) -> dict:
    if not ABSTRACT_API_KEY:
        return None
    clean = re.sub(r'\D', '', phone)
    url = f"https://phonevalidation.abstractapi.com/v1/?api_key={ABSTRACT_API_KEY}&phone={clean}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=30) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get('valid'):
                    return {
                        "country": data.get('country', {}).get('name', '—'),
                        "carrier": data.get('carrier', '—'),
                        "location": data.get('location', '—'),
                        "line_type": data.get('line_type', '—')
                    }
    return None

@safe_request
async def bigdatacloud_lookup(phone: str) -> dict:
    if not BIGDATACLOUD_KEY:
        return None
    clean = re.sub(r'\D', '', phone)
    url = f"https://api.bigdatacloud.net/data/phone-validate?phoneNumber=%2B{clean}&key={BIGDATACLOUD_KEY}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=30) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get('valid'):
                    return {
                        "country": data.get('countryName', '—'),
                        "country_code": data.get('countryCode', '—'),
                        "carrier": data.get('carrier', '—'),
                        "location": data.get('location', '—'),
                        "timezone": data.get('timeZone', '—')
                    }
    return None

@safe_request
async def omkarcloud_lookup(phone: str) -> dict:
    if not OMKAR_KEY:
        return None
    clean = re.sub(r'\D', '', phone)
    url = f"https://carrier-lookup-api.omkar.cloud/lookup?phone=%2B{clean}"
    headers = {"API-Key": OMKAR_KEY}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, timeout=30) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get('is_valid_number'):
                    return {
                        "carrier": data.get('carrier', '—'),
                        "line_type": data.get('line_type', '—'),
                        "country_code": data.get('country_code', '—')
                    }
    return None

@safe_request
async def htmlweb_lookup(phone: str) -> dict:
    clean = re.sub(r'\D', '', phone)
    url = f"https://htmlweb.ru/geo/api.php?json&telcod={clean}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=30) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data:
                    return {
                        "country": data.get('country', '—'),
                        "operator": data.get('operator', '—'),
                        "region": data.get('region', '—'),
                        "timezone": data.get('timezone', '—')
                    }
    return None

@safe_request
async def hlr_lookup(phone: str) -> dict:
    clean = re.sub(r'\D', '', phone)
    url = f"https://smsc.ru/testhlr.php?phone={clean}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=30) as resp:
            if resp.status == 200:
                data = await resp.text()
                return {"status": "✅ Активен" if 'OK' in data else "❌ Не активен"}
    return None

@safe_request
async def ip_api_lookup(ip: str) -> dict:
    url = f"http://ip-api.com/json/{ip}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=30) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get('status') == 'success':
                    return {
                        "country": data.get('country', '—'),
                        "city": data.get('city', '—'),
                        "region": data.get('regionName', '—'),
                        "isp": data.get('isp', '—'),
                        "asn": data.get('as', '—')
                    }
    return None

@safe_request
async def hunter_lookup(email: str) -> dict:
    if not HUNTER_KEY:
        return None
    url = f"https://api.hunter.io/v2/email-verifier?email={email}&api_key={HUNTER_KEY}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=30) as resp:
            if resp.status == 200:
                data = await resp.json()
                result = data.get('data', {})
                return {
                    "status": result.get('status', '—'),
                    "score": result.get('score', 0),
                    "first_name": result.get('first_name', '—'),
                    "last_name": result.get('last_name', '—'),
                    "company": result.get('company', '—')
                }
    return None

# ==================== НОВЫЕ API ====================

@safe_request
async def phonerep_lookup(phone: str) -> dict:
    clean = re.sub(r'\D', '', phone)
    url = f"https://phonerep.com/api?phone={clean}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=30) as resp:
            if resp.status == 200:
                data = await resp.json()
                return {
                    "valid": data.get('valid', False),
                    "country": data.get('country', '—'),
                    "carrier": data.get('carrier', '—'),
                    "line_type": data.get('line_type', '—'),
                    "location": data.get('location', '—'),
                    "spam_risk": data.get('spam_risk', 0)
                }
    return None

@safe_request
async def numlookup_api(phone: str) -> dict:
    clean = re.sub(r'\D', '', phone)
    url = f"https://api.numlookup.com/validate?number={clean}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=30) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get('valid'):
                    return {
                        "country": data.get('country_name', '—'),
                        "carrier": data.get('carrier', '—'),
                        "line_type": data.get('line_type', '—'),
                        "location": data.get('location', '—')
                    }
    return None

@safe_request
async def zlookup_api(phone: str) -> dict:
    clean = re.sub(r'\D', '', phone)
    url = f"https://api.zlookup.com/validate?number={clean}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=30) as resp:
            if resp.status == 200:
                data = await resp.json()
                return {
                    "valid": data.get('valid', False),
                    "country": data.get('country', '—'),
                    "carrier": data.get('carrier', '—')
                }
    return None

@safe_request
async def clearbit_lookup(email: str) -> dict:
    url = f"https://clearbit.com/v2/people/find?email={email}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=30) as resp:
            if resp.status == 200:
                data = await resp.json()
                return {
                    "name": data.get('name', {}).get('fullName', '—'),
                    "company": data.get('employment', {}).get('name', '—'),
                    "location": data.get('geo', {}).get('city', '—'),
                    "twitter": data.get('twitter', {}).get('handle', '—')
                }
    return None

# ==================== НЕЛЕГАЛЬНЫЕ/ПАРСИНГ API ====================

@safe_request
async def hudsonrock_lookup(phone: str) -> dict:
    clean = re.sub(r'\D', '', phone)
    url = f"https://cavalier.hudsonrock.com/api/v1/search-by-username?username={clean}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=30) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get('total_results', 0) > 0:
                    return {
                        "found": True,
                        "total": data.get('total_results', 0),
                        "breaches": data.get('results', [])[:5]
                    }
    return None

@safe_request
async def hibp_lookup(email: str) -> list:
    url = f"https://haveibeenpwned.com/api/v3/breachedaccount/{email}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=30) as resp:
            if resp.status == 200:
                data = await resp.json()
                return [b.get('Name') for b in data]
    return []

@safe_request
async def emailrep_lookup(email: str) -> dict:
    url = f"https://emailrep.io/{email}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=30) as resp:
            if resp.status == 200:
                data = await resp.json()
                return {
                    "reputation": data.get('reputation', '—'),
                    "suspicious": data.get('suspicious', False),
                    "references": data.get('references', 0)
                }
    return None

@safe_request
async def ipinfo_lookup(ip: str) -> dict:
    url = f"https://ipinfo.io/{ip}/json"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=30) as resp:
            if resp.status == 200:
                data = await resp.json()
                return {
                    "country": data.get('country', '—'),
                    "city": data.get('city', '—'),
                    "region": data.get('region', '—'),
                    "org": data.get('org', '—')
                }
    return None

@safe_request
async def github_username_lookup(username: str) -> dict:
    url = f"https://api.github.com/users/{username}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=30) as resp:
            if resp.status == 200:
                data = await resp.json()
                return {
                    "name": data.get('name', '—'),
                    "bio": data.get('bio', '—'),
                    "company": data.get('company', '—'),
                    "location": data.get('location', '—'),
                    "public_repos": data.get('public_repos', 0),
                    "followers": data.get('followers', 0),
                    "following": data.get('following', 0)
                }
    return None

@safe_request
async def telegram_username_lookup(username: str) -> dict:
    try:
        url = f"https://t.me/{username}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=30) as resp:
                if resp.status == 200:
                    return {"exists": True, "url": f"https://t.me/{username}"}
                else:
                    return {"exists": False}
    except:
        pass
    return {"exists": False}

# ==================== ПАРСЕРЫ (С ЗАМЕНЁННЫМ HTML5LIB) ====================

async def parse_site(url: str, selectors: dict, max_results: int = 10) -> list:
    headers = {
        "User-Agent": random.choice([
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
        ]),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    }
    results = []
    try:
        html = None
        if SCRAPERAPI_KEY:
            html = request_via_scraperapi(url)
        if not html:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=30) as resp:
                    if resp.status == 200:
                        html = await resp.text()
        if html:
            # ВАЖНО: ЗАМЕНА НА HTML5LIB
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

# ----- ВСЕ ПАРСЕРЫ (55+) -----

async def freecarrier_lookup(phone: str) -> list:
    clean = re.sub(r'\D', '', phone)
    url = f"https://freecarrierlookup.com/?phone={clean}"
    selectors = {"result": ".result, .card, .info", "title": ".carrier, .operator", "text": ".description", "extra": ".location, .country"}
    return await parse_site(url, selectors, 3)

async def phoneowner_parse(phone: str) -> list:
    clean = re.sub(r'\D', '', phone)
    url = f"https://phoneowner.info/search?q={clean}"
    selectors = {"result": ".result-item, .person", "title": ".name, .owner", "text": ".address, .location", "extra": ".phone, .email"}
    return await parse_site(url, selectors, 5)

async def socialsearch_lookup(query: str) -> list:
    url = f"https://socialsearch.io/?q={query.replace(' ', '+')}"
    selectors = {"result": ".result, .profile, .card", "title": ".name, .title", "link": "a", "text": ".description", "extra": ".url, .handle"}
    return await parse_site(url, selectors, 10)

async def peekyou_lookup(query: str) -> list:
    url = f"https://www.peekyou.com/{query.replace(' ', '_')}"
    selectors = {"result": ".result, .profile, .person", "title": ".name, .fullname", "link": "a", "text": ".bio, .description", "extra": ".location, .phone, .email"}
    return await parse_site(url, selectors, 10)

async def thatsthem_lookup(phone: str) -> list:
    clean = re.sub(r'\D', '', phone)
    url = f"https://thatsthem.com/phone/{clean}"
    selectors = {"result": ".result, .person, .card", "title": ".name, .fullname", "text": ".address, .location", "extra": ".age, .relatives, .phone"}
    return await parse_site(url, selectors, 5)

async def usphonebook_parse(phone: str) -> list:
    clean = re.sub(r'\D', '', phone)
    url = f"https://www.usphonebook.com/phone/{clean}"
    selectors = {"result": ".result, .card, .person", "title": ".name, .fullname", "text": ".address, .location", "extra": ".age, .relatives"}
    return await parse_site(url, selectors, 5)

async def intelius_lookup(phone: str) -> list:
    clean = re.sub(r'\D', '', phone)
    url = f"https://www.intelius.com/phone/{clean}"
    selectors = {"result": ".person, .result, .card", "title": ".name, .fullname", "text": ".address, .location", "extra": ".age, .relatives, .phone, .email"}
    return await parse_site(url, selectors, 5)

async def beenverified_lookup(phone: str) -> list:
    clean = re.sub(r'\D', '', phone)
    url = f"https://www.beenverified.com/phone/{clean}"
    selectors = {"result": ".person, .result, .card", "title": ".name, .fullname", "text": ".address, .location", "extra": ".age, .relatives, .phone, .email, .social"}
    return await parse_site(url, selectors, 5)

async def instantcheckmate_lookup(phone: str) -> list:
    clean = re.sub(r'\D', '', phone)
    url = f"https://www.instantcheckmate.com/phone/{clean}"
    selectors = {"result": ".person, .result, .card", "title": ".name, .fullname", "text": ".address, .location", "extra": ".criminal, .age, .relatives"}
    return await parse_site(url, selectors, 5)

async def spydialer_lookup(phone: str) -> list:
    clean = re.sub(r'\D', '', phone)
    url = f"https://www.spydialer.com/phone/{clean}"
    selectors = {"result": ".result, .card, .person", "title": ".name, .owner", "text": ".carrier, .location", "extra": ".email, .address"}
    return await parse_site(url, selectors, 3)

async def whitepages_premium_parse(phone: str) -> list:
    clean = re.sub(r'\D', '', phone)
    url = f"https://www.whitepages.com/phone/{clean}"
    selectors = {"result": ".person, .result, .card", "title": ".name, .fullname", "text": ".address, .location", "extra": ".age, .relatives, .email"}
    return await parse_site(url, selectors, 5)

async def pipl_lookup(query: str) -> list:
    url = f"https://pipl.com/search/?q={query.replace(' ', '+')}"
    selectors = {"result": ".result, .person, .card", "title": ".name, .fullname", "link": "a", "text": ".bio, .description", "extra": ".location, .email, .phone, .social"}
    return await parse_site(url, selectors, 15)

async def fouroneone_lookup(phone: str) -> list:
    clean = re.sub(r'\D', '', phone)
    url = f"https://www.411.com/phone/{clean}"
    selectors = {"result": ".result, .person, .card", "title": ".name, .fullname", "text": ".address, .location", "extra": ".age, .relatives"}
    return await parse_site(url, selectors, 5)

async def yellowpages_lookup(phone: str) -> list:
    clean = re.sub(r'\D', '', phone)
    url = f"https://www.yellowpages.com/phone/{clean}"
    selectors = {"result": ".result, .business, .person", "title": ".name, .title", "text": ".address, .location", "extra": ".phone, .email"}
    return await parse_site(url, selectors, 5)

async def truecaller_parse(phone: str) -> list:
    clean = re.sub(r'\D', '', phone)
    url = f"https://www.truecaller.com/search/{clean}"
    selectors = {"result": ".profile, .card, .result-item", "title": ".name, .title", "text": ".description", "extra": ".phone, .location"}
    return await parse_site(url, selectors, 5)

async def spokeo_parse(phone: str) -> list:
    clean = re.sub(r'\D', '', phone)
    url = f"https://www.spokeo.com/{clean}/search"
    selectors = {"result": ".result-item, .person-item", "title": ".name, .title", "text": ".description", "extra": ".address"}
    return await parse_site(url, selectors, 5)

async def whitepages_parse(phone: str) -> list:
    clean = re.sub(r'\D', '', phone)
    url = f"https://www.whitepages.com/phone/{clean}"
    selectors = {"result": ".card, .result-item", "title": ".name, .title", "text": ".description", "extra": ".address"}
    return await parse_site(url, selectors, 5)

async def fastpeoplesearch_parse(phone: str) -> list:
    clean = re.sub(r'\D', '', phone)
    url = f"https://www.fastpeoplesearch.com/phone/{clean}"
    selectors = {"result": ".result, .person-item", "title": ".name, .title", "text": ".description", "extra": ".address"}
    return await parse_site(url, selectors, 5)

async def xray_lookup(query: str) -> list:
    url = f"https://x-ray.contact/search?q={query}"
    selectors = {"result": ".result-item, .social-link, .profile-item", "title": ".title, .name", "link": "a", "text": ".description", "extra": ".extra"}
    return await parse_site(url, selectors, 15)

async def idcrawl_lookup(query: str) -> list:
    url = f"https://idcrawl.com/{query}"
    selectors = {"result": ".result-item, .profile-item", "title": ".title, .name", "link": "a", "text": ".description", "extra": ".extra"}
    return await parse_site(url, selectors, 15)

async def syncme_lookup(phone: str) -> list:
    url = f"https://sync.me/search?q={phone}"
    selectors = {"result": ".profile, .card, .result-item", "title": ".name, .title", "text": ".description", "extra": ".phone, .location"}
    return await parse_site(url, selectors, 5)

async def whoseno_lookup(phone: str) -> list:
    url = f"https://whoseno.com/search?q={phone}"
    selectors = {"result": ".result, .card", "title": ".name, .title", "text": ".description", "extra": ".phone"}
    return await parse_site(url, selectors, 5)

async def truepeoplesearch_lookup(query: str) -> list:
    if re.search(r'\d', query):
        url = f"https://truepeoplesearch.com/results?phoneno={query}"
    else:
        url = f"https://truepeoplesearch.com/results?name={query.replace(' ', '+')}"
    selectors = {"result": ".card, .person-item", "title": ".name, .title", "text": ".description", "extra": ".address, .phone, .relatives"}
    return await parse_site(url, selectors, 10)

async def duckduckgo_search(query: str) -> list:
    url = f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}"
    selectors = {"result": ".result", "title": ".result__title a", "link": "a", "text": ".result__snippet"}
    return await parse_site(url, selectors, 5)

async def wikipedia_lookup(query: str) -> list:
    url = f"https://ru.wikipedia.org/wiki/{query.replace(' ', '_')}"
    selectors = {"result": ".mw-parser-output p", "title": "h1.firstHeading", "text": ".mw-parser-output p"}
    return await parse_site(url, selectors, 3)

async def fssp_lookup(fio: str) -> dict:
    try:
        params = {"name": fio}
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api-ip.fssp.gov.ru/api/v1.0/search/physical", params=params, timeout=30) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("response", {}).get("count"):
                        return {
                            "found": True,
                            "count": data["response"]["count"],
                            "debts": data["response"].get("items", [])[:3]
                        }
    except:
        pass
    return {"found": False}

async def freephonenum_parse(phone: str) -> list:
    clean = re.sub(r'\D', '', phone)
    url = f"https://www.freephonenum.com/search?q={clean}"
    selectors = {"result": ".result, .card, .phone-info", "title": ".title, .name", "text": ".description, .text"}
    return await parse_site(url, selectors, 3)

async def phonesearch_parse(phone: str) -> list:
    clean = re.sub(r'\D', '', phone)
    url = f"https://www.phonesearch.com/phone/{clean}"
    selectors = {"result": ".result, .person, .card", "title": ".name, .title", "extra": ".address, .location"}
    return await parse_site(url, selectors, 3)

async def callerid_parse(phone: str) -> list:
    clean = re.sub(r'\D', '', phone)
    url = f"https://www.callerid.com/phone/{clean}"
    selectors = {"result": ".result, .card, .info", "title": ".name, .title", "extra": ".spam, .warning"}
    return await parse_site(url, selectors, 3)

async def numberlookup_api(phone: str) -> dict:
    clean = re.sub(r'\D', '', phone)
    url = f"https://numberlookupapi.com/api?number={clean}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=30) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get('valid'):
                    return {
                        "country": data.get('country', '—'),
                        "carrier": data.get('carrier', '—'),
                        "line_type": data.get('line_type', '—')
                    }
    return None

async def crtsh_lookup(domain: str) -> list:
    url = f"https://crt.sh/?q={domain}&output=json"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=30) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data:
                    return [{"domain": item.get('name_value', '—')} for item in data[:5]]
    return []

async def whois_lookup(domain: str) -> dict:
    url = f"https://api.whois.vu/?q={domain}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=30) as resp:
            if resp.status == 200:
                data = await resp.json()
                return {
                    "registrar": data.get('registrar', '—'),
                    "creation_date": data.get('creation_date', '—'),
                    "expiration_date": data.get('expiration_date', '—')
                }
    return None

async def zippopotam_lookup(postal_code: str, country: str = "RU") -> dict:
    url = f"https://api.zippopotam.us/{country}/{postal_code}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=30) as resp:
            if resp.status == 200:
                data = await resp.json()
                return {
                    "country": data.get('country', '—'),
                    "places": data.get('places', [])[:3]
                }
    return None

async def leadfinder_lookup(niche: str, city: str = "Moscow") -> dict:
    try:
        proc = await asyncio.create_subprocess_exec(
            "npx", "leadfinder-api", "--niche", niche, "--city", city, "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        if stdout:
            data = json.loads(stdout)
            if data.get('results'):
                return {
                    "found": True,
                    "total": len(data['results']),
                    "businesses": data['results'][:5],
                    "source": "LeadFinder"
                }
    except:
        pass
    return None

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
            ("numverify", numverify_lookup(query)),
            ("veriphone", veriphone_lookup(query)),
            ("abstractapi", abstractapi_lookup(query)),
            ("bigdatacloud", bigdatacloud_lookup(query)),
            ("omkarcloud", omkarcloud_lookup(query)),
            ("htmlweb", htmlweb_lookup(query)),
            ("hlr", hlr_lookup(query)),
            ("numberlookup", numberlookup_api(query)),
            ("phonerep", phonerep_lookup(query)),
            ("numlookup", numlookup_api(query)),
            ("zlookup", zlookup_api(query)),
            ("hudsonrock", hudsonrock_lookup(query)),
            ("leadfinder", leadfinder_lookup(query)),
            ("freecarrier", freecarrier_lookup(query)),
            ("phoneowner", phoneowner_parse(query)),
            ("socialsearch", socialsearch_lookup(query)),
            ("peekyou", peekyou_lookup(query)),
            ("thatsthem", thatsthem_lookup(query)),
            ("usphonebook", usphonebook_parse(query)),
            ("intelius", intelius_lookup(query)),
            ("beenverified", beenverified_lookup(query)),
            ("instantcheckmate", instantcheckmate_lookup(query)),
            ("spydialer", spydialer_lookup(query)),
            ("whitepages_premium", whitepages_premium_parse(query)),
            ("pipl", pipl_lookup(query)),
            ("fouroneone", fouroneone_lookup(query)),
            ("yellowpages", yellowpages_lookup(query)),
            ("xray", xray_lookup(query)),
            ("idcrawl", idcrawl_lookup(query)),
            ("syncme", syncme_lookup(query)),
            ("whoseno", whoseno_lookup(query)),
            ("truepeoplesearch", truepeoplesearch_lookup(query)),
            ("truecaller", truecaller_parse(query)),
            ("spokeo", spokeo_parse(query)),
            ("whitepages", whitepages_parse(query)),
            ("fastpeoplesearch", fastpeoplesearch_parse(query)),
            ("freephonenum", freephonenum_parse(query)),
            ("phonesearch", phonesearch_parse(query)),
            ("callerid", callerid_parse(query)),
            ("duckduckgo", duckduckgo_search(query)),
            ("wikipedia", wikipedia_lookup(query)),
        ]
        if len(query.split()) >= 2:
            tasks.append(("fssp", fssp_lookup(query)))
    
    elif qtype == "email":
        tasks = [
            ("emailrep", emailrep_lookup(query)),
            ("hibp", hibp_lookup(query)),
            ("hudsonrock", hudsonrock_lookup(query)),
            ("hunter", hunter_lookup(query)),
            ("clearbit", clearbit_lookup(query)),
            ("socialsearch", socialsearch_lookup(query)),
            ("pipl", pipl_lookup(query)),
            ("duckduckgo", duckduckgo_search(query)),
            ("leadfinder", leadfinder_lookup(query)),
        ]
    
    elif qtype == "domain":
        tasks = [
            ("crtsh", crtsh_lookup(query)),
            ("whois", whois_lookup(query)),
            ("duckduckgo", duckduckgo_search(query)),
        ]
    
    elif qtype == "username":
        tasks = [
            ("xray", xray_lookup(query)),
            ("idcrawl", idcrawl_lookup(query)),
            ("socialsearch", socialsearch_lookup(query)),
            ("peekyou", peekyou_lookup(query)),
            ("pipl", pipl_lookup(query)),
            ("duckduckgo", duckduckgo_search(query)),
            ("github", github_username_lookup(query)),
            ("telegram", telegram_username_lookup(query)),
        ]
    
    elif qtype == "ip":
        tasks = [
            ("ipinfo", ipinfo_lookup(query)),
            ("ip_api", ip_api_lookup(query)),
            ("duckduckgo", duckduckgo_search(query)),
        ]
    else:
        tasks = [("duckduckgo", duckduckgo_search(query))]
    
    if tasks:
        results = await asyncio.gather(*[task for _, task in tasks], return_exceptions=True)
        for idx, (name, _) in enumerate(tasks):
            if results[idx] and not isinstance(results[idx], Exception):
                result["sources"][name] = results[idx]
                if isinstance(results[idx], list):
                    total += len(results[idx])
                else:
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
        if items:
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        all_results.append(item)
                    else:
                        all_results.append({"title": str(item)})
            elif isinstance(items, dict):
                all_results.append(items)
            else:
                all_results.append({"title": str(items)})
    
    title = generate_otob_title(query, qtype)
    
    html = f"""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            background: #0a0a0a;
            color: #c0c0c0;
            font-family: 'Segoe UI', 'Courier New', monospace;
            padding: 30px 20px;
            line-height: 1.6;
            min-height: 100vh;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: #0d0d0d;
            border-radius: 16px;
            padding: 40px 45px;
            border: 1px solid #1a1a1a;
            box-shadow: 0 0 40px rgba(0,255,0,0.03), 0 0 80px rgba(0,255,0,0.01);
            position: relative;
        }}
        .watermark {{
            position: absolute;
            top: 20px;
            right: 30px;
            z-index: 10;
            opacity: 0.15;
            user-select: none;
            pointer-events: none;
            display: flex;
            flex-direction: column;
            align-items: center;
        }}
        .watermark svg {{
            width: 60px;
            height: 70px;
        }}
        .watermark .text {{
            color: #00ff00;
            font-size: 14px;
            font-weight: 900;
            letter-spacing: 4px;
            margin-top: 4px;
            text-transform: uppercase;
            font-family: 'Courier New', monospace;
        }}
        .header {{
            border-bottom: 2px solid #1a2a1a;
            padding-bottom: 20px;
            margin-bottom: 28px;
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            flex-wrap: wrap;
        }}
        .header h1 {{
            font-size: 28px;
            font-weight: 700;
            color: #00cc00;
            letter-spacing: 2px;
            text-shadow: 0 0 20px rgba(0,255,0,0.1);
        }}
        .header h1 span {{
            color: #00ff00;
            background: #0a1a0a;
            padding: 0 12px;
            border-radius: 4px;
            border: 1px solid #1a3a1a;
        }}
        .header .sub {{
            color: #4a6a4a;
            font-size: 13px;
            margin-top: 6px;
            font-family: 'Courier New', monospace;
        }}
        .badge {{
            display: inline-block;
            background: #0a1a0a;
            padding: 6px 18px;
            border-radius: 8px;
            font-size: 13px;
            color: #00ff00;
            border: 1px solid #1a3a1a;
            font-weight: 600;
            letter-spacing: 1px;
        }}
        .badge-success {{ background: #0a1a0a; color: #00ff00; border-color: #1a3a1a; }}
        
        .stats-bar {{
            display: flex;
            gap: 20px;
            flex-wrap: wrap;
            margin: 20px 0 25px 0;
            padding: 15px 20px;
            background: #0a0a0a;
            border-radius: 8px;
            border: 1px solid #1a2a1a;
        }}
        .stats-bar .stat {{
            font-size: 13px;
            color: #4a6a4a;
            font-family: 'Courier New', monospace;
        }}
        .stats-bar .stat strong {{
            color: #00cc00;
        }}
        
        .result-item {{
            margin: 14px 0;
            padding: 16px 22px;
            background: #0a0a0a;
            border-radius: 10px;
            border-left: 4px solid #1a3a1a;
            transition: 0.25s;
            border: 1px solid #111a11;
        }}
        .result-item:hover {{
            background: #0d150d;
            border-left-color: #00ff00;
            border-color: #1a3a1a;
            box-shadow: 0 0 30px rgba(0,255,0,0.02);
        }}
        .result-item .title {{
            font-size: 17px;
            font-weight: 500;
            color: #d0e0d0;
            font-family: 'Segoe UI', sans-serif;
        }}
        .result-item .title a {{
            color: #00cc66;
            text-decoration: none;
            border-bottom: 1px dotted #1a3a1a;
        }}
        .result-item .title a:hover {{
            color: #00ff66;
        }}
        .result-item .text {{
            font-size: 14px;
            color: #7a9a7a;
            margin-top: 6px;
            font-family: 'Segoe UI', sans-serif;
        }}
        .result-item .extra {{
            font-size: 13px;
            color: #4a7a4a;
            margin-top: 4px;
            font-family: 'Courier New', monospace;
        }}
        .result-item .index {{
            display: inline-block;
            background: #0a1a0a;
            color: #00aa44;
            font-size: 12px;
            padding: 2px 14px;
            border-radius: 6px;
            margin-right: 12px;
            border: 1px solid #1a2a1a;
            font-weight: 600;
        }}
        .result-item .source-tag {{
            display: inline-block;
            background: #0a1a0a;
            color: #4a8a4a;
            font-size: 10px;
            padding: 2px 10px;
            border-radius: 4px;
            border: 1px solid #1a2a1a;
            margin-left: 10px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .empty {{ 
            color: #3a5a3a; 
            font-style: italic; 
            font-size: 15px; 
            padding: 30px; 
            text-align: center;
            border: 1px dashed #1a2a1a;
            border-radius: 8px;
        }}
        .footer {{ 
            margin-top: 30px; 
            padding-top: 20px; 
            border-top: 1px solid #1a2a1a; 
            font-size: 12px; 
            color: #2a4a2a; 
            text-align: center;
            font-family: 'Courier New', monospace;
        }}
        .footer a {{ 
            color: #3a6a3a; 
            text-decoration: none; 
            border-bottom: 1px dotted #1a3a1a;
        }}
        .footer a:hover {{ color: #00cc66; }}
        
        .glitch-text {{
            color: #00ff00;
            text-shadow: 0 0 10px rgba(0,255,0,0.2);
            animation: glitch 3s infinite;
        }}
        @keyframes glitch {{
            0%, 100% {{ opacity: 1; }}
            50% {{ opacity: 0.8; }}
        }}
        
        .scanline {{
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            pointer-events: none;
            background: repeating-linear-gradient(
                0deg,
                transparent,
                transparent 2px,
                rgba(0,255,0,0.003) 2px,
                rgba(0,255,0,0.003) 4px
            );
            z-index: 9999;
        }}
        
        @media (max-width: 600px) {{ 
            .container {{ padding: 16px; }} 
            .header h1 {{ font-size: 20px; }}
            .watermark {{ display: none; }}
            .stats-bar {{ flex-direction: column; gap: 6px; }}
        }}
    </style>
</head>
<body>
    <div class="scanline"></div>
    <div class="container">
        <div class="watermark">
            <svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
                <circle cx="50" cy="50" r="42" stroke="#00ff00" stroke-width="2.5" fill="none"/>
                <circle cx="50" cy="50" r="30" stroke="#00ff00" stroke-width="1.5" fill="none" opacity="0.3"/>
                <circle cx="50" cy="50" r="18" stroke="#00ff00" stroke-width="1.5" fill="none" opacity="0.2"/>
                <path d="M50 8 L50 92" stroke="#00ff00" stroke-width="1" opacity="0.15"/>
                <path d="M8 50 L92 50" stroke="#00ff00" stroke-width="1" opacity="0.15"/>
                <circle cx="50" cy="50" r="4" fill="#00ff00" opacity="0.5"/>
                <text x="50" y="38" font-family="Courier New" font-size="14" fill="#00ff00" text-anchor="middle" opacity="0.3">🔍</text>
            </svg>
            <div class="text">OTOB</div>
        </div>
        <div class="header">
            <div>
                <h1>🔍 OTOB <span>OSINT</span></h1>
                <div class="sub">⚡ Запрос: {query} · Тип: {qtype} · {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</div>
                <div class="sub" style="color:#2a5a2a; margin-top:2px;">🛡️ 55+ источников · Глобальный поиск</div>
            </div>
            <div><span class="badge badge-success">🎯 НАЙДЕНО: {total}</span></div>
        </div>
        
        <div class="stats-bar">
            <span class="stat">📊 Всего результатов: <strong>{total}</strong></span>
            <span class="stat">🔍 Источников: <strong>{len(sources)}</strong></span>
            <span class="stat">⚡ Статус: <strong style="color:#00ff00;">АКТИВЕН</strong></span>
        </div>
"""
    
    if all_results:
        for idx, item in enumerate(all_results[:30], 1):
            title = item.get('title', '—')[:80]
            text = item.get('text', '')[:250]
            extra = item.get('extra', '')
            link = item.get('link', '')
            
            details = []
            for key, value in item.items():
                if key not in ['title', 'text', 'extra', 'link'] and value:
                    if isinstance(value, str) and value != '—' and value != '':
                        details.append(f"{key}: {value}")
                    elif isinstance(value, list) and value:
                        details.append(f"{key}: {', '.join(str(v) for v in value[:5])}")
                    elif isinstance(value, dict) and value:
                        for k, v in value.items():
                            if v and v != '—':
                                details.append(f"{key}.{k}: {v}")
            
            html += f"""
        <div class="result-item">
            <div class="title">
                <span class="index">#{idx}</span>
                {title}
                {f'<a href="{link}" target="_blank">🔗</a>' if link else ''}
            </div>
"""
            if text and text != '—' and text != '':
                html += f"            <div class=\"text\">{text}</div>\n"
            if extra:
                html += f"            <div class=\"extra\">📎 {extra}</div>\n"
            if details:
                for detail in details[:6]:
                    html += f"            <div class=\"extra\">• {detail}</div>\n"
            html += "        </div>\n"
        
        html += f"""
        <div style="text-align:center; margin-top:20px; padding:12px; border:1px solid #1a2a1a; border-radius:8px; color:#4a6a4a; font-size:13px;">
            📊 Показано {min(len(all_results), 30)} из {total} результатов
        </div>
"""
    else:
        html += '<div class="empty">❌ Ничего не найдено</div>'
    
    html += f"""
        <div class="footer">
            🛡️ OTOB — Osint Tool Olimpov Bot · <a href="https://t.me/OTOBsearch" target="_blank">@OTOBsearch</a>
        </div>
    </div>
</body>
</html>
"""
    return html

# ==================== МЕНЮ ====================

def main_menu_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🔍 ГЛОБАЛЬНЫЙ ПОИСК", callback_data="global_search")
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
        types.InlineKeyboardButton("⚡ МОЙ ПРОФИЛЬ", callback_data="menu_profile"),
        types.InlineKeyboardButton("📊 БАЛАНС", callback_data="menu_balance")
    )
    markup.add(
        types.InlineKeyboardButton("❓ ПОМОЩЬ", callback_data="menu_help"),
        types.InlineKeyboardButton("🛡️ КАНАЛ", url="https://t.me/OTOBsearch")
    )
    return markup

def functions_menu_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🌐 Глобальный поиск", callback_data="global_search")
    )
    markup.add(
        types.InlineKeyboardButton("📧 Email", callback_data="email_search"),
        types.InlineKeyboardButton("📱 Телефон", callback_data="phone_search")
    )
    markup.add(
        types.InlineKeyboardButton("👤 Username", callback_data="username_search"),
        types.InlineKeyboardButton("🌐 IP/Домен", callback_data="domain_search")
    )
    markup.add(
        types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back")
    )
    return markup

# ==================== ОБРАБОТЧИКИ ====================

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    try:
        bot.answer_callback_query(call.id)
        
        if call.data == "menu_back":
            bot.edit_message_text(
                "🔍 *OTOB — Osint Tool Olimpov Bot*\n\n"
                "🕵️ *Глобальный OSINT-поиск*\n"
                "55+ источников · Мгновенный отчёт\n\n"
                "⚡ *Выбери действие:*",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=main_menu_keyboard()
            )
            return
        
        if call.data == "menu_functions":
            bot.edit_message_text(
                "🔍 *Выбери функцию:*\n\n"
                "📌 *Основной поиск:*\n"
                "• 🌐 Глобальный — номер, email, ФИО, IP, домен\n\n"
                "📌 *Быстрый поиск:*\n"
                "• 📧 Email — проверка утечек\n"
                "• 📱 Телефон — оператор, регион, владелец\n"
                "• 👤 Username — поиск в соцсетях\n"
                "• 🌐 IP/Домен — геолокация, WHOIS",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=functions_menu_keyboard()
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
                f"📊 Использовано: {user_data['searches_today']}/3\n"
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
                f"🔍 Использовано: {used}/3\n"
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
                "• Отправь номер, email, никнейм, IP или домен\n"
                "• Глобальный поиск — всё в одном запросе\n\n"
                "📊 *Лимит:* 3 поиска в день (сброс в 00:00 МСК)\n"
                "👑 *Админ:* безлимитный доступ\n\n"
                "🛡️ *Канал:* @OTOBsearch\n"
                "🧑‍💻 *Разработчик:* @OTOBsearch",
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
                "• 🌍 Домен: `example.com`\n"
                "• 📝 Любой текст\n\n"
                "⚡ *55+ OSINT-источников*\n"
                "🕵️ Имя · Адрес · Оператор · Соцсети · Утечки",
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
                "Отправь email для проверки утечек.\n\n"
                "Пример: `user@example.com`\n\n"
                "🔍 Проверка в базах утечек (HIBP, Hudson Rock)\n"
                "📊 Репутация (EmailRep)\n"
                "🏢 Компания (Clearbit)",
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
                "🔍 Оператор · Страна · Регион\n"
                "🕵️ Владелец · Адрес · Соцсети\n"
                "⚡ 30+ источников по номеру",
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
                "Отправь никнейм для поиска в соцсетях.\n\n"
                "Пример: `username`\n\n"
                "🔍 GitHub · Telegram · Соцсети\n"
                "🕵️ Pipl · PeekYou · SocialSearch",
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
                "🔍 Геолокация · WHOIS · SSL-сертификаты",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("⬅️ Назад", callback_data="menu_back")
                )
            )
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
            f"🔍 *OTOB — Osint Tool Olimpov Bot*\n\n"
            f"🕵️ Привет, {message.from_user.first_name}!\n"
            f"⚡ Глобальный OSINT-поиск\n\n"
            f"📊 *Осталось:* {remaining}/3\n\n"
            f"📌 *Выбери действие:*",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard()
        )
    except Exception as e:
        logger.error(f"Start error: {e}")

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
            total = (3 - today) + extra
            text += f"• `{user_id}` — @{username or 'нет'} | запросов: {total}\n"
        bot.reply_to(message, text, parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"⚠️ Ошибка: {e}")

# ==================== ОБРАБОТЧИК ТЕКСТА ====================

@bot.message_handler(func=lambda message: True)
def handle_text(message):
    try:
        text = message.text.strip()
        if not text or text.startswith('/'):
            return
        
        user_id = message.from_user.id
        chat_id = message.chat.id
        
        if not can_search(user_id):
            bot.reply_to(message, "❌ *Лимит поисков исчерпан!*\n\n⏰ Сброс в 00:00 МСК", parse_mode="Markdown")
            return
        
        start_time = time.time()
        msg = bot.reply_to(
            message,
            "⏳ *Глобальный поиск по 55+ источникам...*\n"
            "⏱️ Время: ~15-30 секунд\n"
            "🕵️ Идёт сканирование...",
            parse_mode="Markdown"
        )
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        data = loop.run_until_complete(global_lookup(text))
        loop.close()
        
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
            bot.send_document(
                chat_id,
                f,
                caption=f"📊 *OSINT-отчёт*\n\n"
                        f"🔍 Запрос: `{text}`\n"
                        f"📌 Найдено: **{total}**\n"
                        f"🔍 Осталось: **{remaining}/3**\n"
                        f"⏱️ Время: **{elapsed:.1f} сек**\n"
                        f"🛡️ @OTOBsearch",
                parse_mode="Markdown"
            )
        
        os.remove(filename)
        bot.delete_message(chat_id, msg.message_id)
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        try:
            bot.send_message(message.chat.id, f"⚠️ Ошибка: {str(e)[:100]}")
        except:
            pass

# ==================== ЗАПУСК ====================

if __name__ == "__main__":
    init_db()
    logger.info("🚀 OTOB бот запускается...")
    logger.info("🛡️ Канал: @OTOBsearch")
    bot.remove_webhook()
    bot.infinity_polling(timeout=60, long_polling_timeout=30)
