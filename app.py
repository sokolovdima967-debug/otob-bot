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
bot.remove_webhook()  # Убираем вебхук, чтобы избежать 409

# ==================== HTTP-СЕРВЕР (С ПОДДЕРЖКОЙ HEAD) ====================

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
        """Render использует HEAD-запросы для проверки доступности"""
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
        f"OTOB — Osint Tool Olimpov Bot | {qtype.upper()} | {query}",
        f"OTOB | {query} | {qtype.upper()} | OSINT-отчёт",
        f"OSINT Tool Olimpov Bot — OTOB | {qtype} | {query}",
        f"OTOB — глобальный поиск | {query} | {qtype.upper()}",
    ]
    return random.choice(templates)

def safe_request(func):
    """Декоратор для безопасного выполнения запросов с обработкой всех ошибок"""
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

# ==================== API ФУНКЦИИ ====================

@safe_request
async def numverify_lookup(phone: str) -> dict:
    if not NUMVERIFY_KEY:
        return None
    try:
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
    except:
        pass
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
async def hibp_lookup(email: str) -> list:
    url = f"https://haveibeenpwned.com/api/v3/breachedaccount/{email}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=30) as resp:
            if resp.status == 200:
                data = await resp.json()
                return [b.get('Name') for b in data]
    return []

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
async def crtsh_lookup(domain: str) -> list:
    url = f"https://crt.sh/?q={domain}&output=json"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=30) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data:
                    return [{"domain": item.get('name_value', '—')} for item in data[:5]]
    return []

@safe_request
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

@safe_request
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

# ==================== НОВЫЕ ФУНКЦИИ ====================

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

async def telegram_bot_parser(phone: str) -> dict:
    results = {}
    bots = {
        "sherlock": "https://sherlock-tg.vercel.app/api/search?q={phone}",
        "osant": "https://osant-bot.vercel.app/api/phone?number={phone}",
    }
    for name, url_template in bots.items():
        try:
            url = url_template.format(phone=phone)
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=30) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        results[name] = data
        except:
            pass
    if results:
        return {"found": True, "results": results, "source": "TelegramBots"}
    return None

async def freephonenum_parse(phone: str) -> list:
    try:
        clean = re.sub(r'\D', '', phone)
        url = f"https://www.freephonenum.com/search?q={clean}"
        headers = {"User-Agent": "Mozilla/5.0"}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=30) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    results = []
                    for item in soup.select('.result, .card, .phone-info')[:3]:
                        title = item.select_one('.title, .name')
                        text = item.select_one('.description, .text')
                        if title:
                            results.append({
                                "title": title.get_text(strip=True),
                                "text": text.get_text(strip=True) if text else "—"
                            })
                    return results
    except:
        pass
    return []

async def phonesearch_parse(phone: str) -> list:
    try:
        clean = re.sub(r'\D', '', phone)
        url = f"https://www.phonesearch.com/phone/{clean}"
        headers = {"User-Agent": "Mozilla/5.0"}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=30) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    results = []
                    for item in soup.select('.result, .person, .card')[:3]:
                        name = item.select_one('.name, .title')
                        address = item.select_one('.address, .location')
                        if name:
                            results.append({
                                "title": name.get_text(strip=True),
                                "extra": address.get_text(strip=True) if address else "—"
                            })
                    return results
    except:
        pass
    return []

async def callerid_parse(phone: str) -> list:
    try:
        clean = re.sub(r'\D', '', phone)
        url = f"https://www.callerid.com/phone/{clean}"
        headers = {"User-Agent": "Mozilla/5.0"}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=30) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    results = []
                    for item in soup.select('.result, .card, .info')[:3]:
                        name = item.select_one('.name, .title')
                        spam = item.select_one('.spam, .warning')
                        if name:
                            results.append({
                                "title": name.get_text(strip=True),
                                "extra": spam.get_text(strip=True) if spam else "Не спам"
                            })
                    return results
    except:
        pass
    return []

async def numberlookup_api(phone: str) -> dict:
    try:
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
    except:
        pass
    return None

# ==================== ПАРСЕРЫ ====================

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
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=30) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, 'html.parser')
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
            ("hudsonrock", hudsonrock_lookup(query)),
            ("numberlookup", numberlookup_api(query)),
            ("leadfinder", leadfinder_lookup(query)),
            ("telegram_bots", telegram_bot_parser(query)),
            ("freephonenum", freephonenum_parse(query)),
            ("phonesearch", phonesearch_parse(query)),
            ("callerid", callerid_parse(query)),
            ("xray", xray_lookup(query)),
            ("idcrawl", idcrawl_lookup(query)),
            ("syncme", syncme_lookup(query)),
            ("whoseno", whoseno_lookup(query)),
            ("truepeoplesearch", truepeoplesearch_lookup(query)),
            ("truecaller", truecaller_parse(query)),
            ("spokeo", spokeo_parse(query)),
            ("whitepages", whitepages_parse(query)),
            ("fastpeoplesearch", fastpeoplesearch_parse(query)),
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
            font-family: 'Segoe UI', system-ui, sans-serif;
            padding: 30px 20px;
            line-height: 1.6;
            min-height: 100vh;
        }}
        .container {{
            max-width: 1000px;
            margin: 0 auto;
            background: #121212;
            border-radius: 12px;
            padding: 35px 40px;
            border: 1px solid #2a0a0a;
            box-shadow: 0 20px 60px rgba(0,0,0,0.9);
            position: relative;
        }}
        .watermark {{
            position: absolute;
            top: 20px;
            left: 30px;
            z-index: 10;
            opacity: 0.3;
            user-select: none;
            pointer-events: none;
            display: flex;
            flex-direction: column;
            align-items: center;
        }}
        .watermark svg {{
            width: 55px;
            height: 65px;
        }}
        .watermark .text {{
            color: #8a2a2a;
            font-size: 13px;
            font-weight: 700;
            letter-spacing: 3px;
            margin-top: 2px;
            text-transform: uppercase;
            font-family: 'Segoe UI', sans-serif;
        }}
        .header {{
            border-bottom: 2px solid #2a0a0a;
            padding-bottom: 18px;
            margin-bottom: 22px;
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            flex-wrap: wrap;
            padding-left: 80px;
        }}
        .header h1 {{
            font-size: 26px;
            font-weight: 700;
            color: #e8d0d0;
            letter-spacing: 1px;
        }}
        .header h1 span {{
            color: #8a2a2a;
        }}
        .header .sub {{
            color: #7a4a4a;
            font-size: 13px;
            margin-top: 4px;
        }}
        .badge {{
            display: inline-block;
            background: #1a0a0a;
            padding: 4px 14px;
            border-radius: 6px;
            font-size: 12px;
            color: #cc6a6a;
            border: 1px solid #3a1a1a;
        }}
        .badge-success {{ background: #1a0a0a; color: #cc6a6a; border-color: #3a1a1a; }}
        .result-item {{
            margin: 12px 0;
            padding: 14px 20px;
            background: #0e0e0e;
            border-radius: 8px;
            border-left: 4px solid #4a1a1a;
            transition: 0.2s;
        }}
        .result-item:hover {{
            background: #181010;
            border-left-color: #7a2a2a;
        }}
        .result-item .title {{
            font-size: 16px;
            font-weight: 500;
            color: #d8c8c8;
        }}
        .result-item .title a {{
            color: #cc7a7a;
            text-decoration: none;
            border-bottom: 1px dotted #4a2a2a;
        }}
        .result-item .title a:hover {{
            color: #e8a0a0;
        }}
        .result-item .text {{
            font-size: 14px;
            color: #9a8a8a;
            margin-top: 6px;
        }}
        .result-item .extra {{
            font-size: 13px;
            color: #7a4a4a;
            margin-top: 4px;
        }}
        .result-item .index {{
            display: inline-block;
            background: #1a0a0a;
            color: #8a4a4a;
            font-size: 12px;
            padding: 1px 12px;
            border-radius: 4px;
            margin-right: 10px;
        }}
        .empty {{ color: #5a3a3a; font-style: italic; font-size: 14px; padding: 20px; text-align: center; }}
        .stats {{ margin-top: 20px; padding: 12px 20px; background: #0e0e0e; border-radius: 8px; border: 1px solid #1a0a0a; color: #7a4a4a; font-size: 13px; text-align: center; }}
        .footer {{ margin-top: 25px; padding-top: 16px; border-top: 1px solid #1a0a0a; font-size: 12px; color: #4a2a2a; text-align: center; }}
        .footer a {{ color: #7a3a3a; text-decoration: none; }}
        .footer a:hover {{ color: #aa5a5a; }}
        @media (max-width: 600px) {{ .container {{ padding: 16px; }} .header {{ padding-left: 0; padding-top: 70px; }} .watermark {{ top: 10px; left: 15px; }} .watermark svg {{ width: 40px; height: 50px; }} }}
    </style>
</head>
<body>
    <div class="container">
        <div class="watermark">
            <svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
                <polygon points="50,5 5,90 95,90" stroke="#8a2a2a" stroke-width="2.5" fill="none"/>
                <line x1="50" y1="5" x2="50" y2="90" stroke="#5a2a2a" stroke-width="0.8" stroke-dasharray="4,4"/>
                <line x1="18" y1="70" x2="82" y2="70" stroke="#5a2a2a" stroke-width="0.8" stroke-dasharray="4,4"/>
                <line x1="27" y1="50" x2="73" y2="50" stroke="#5a2a2a" stroke-width="0.8" stroke-dasharray="4,4"/>
                <ellipse cx="50" cy="45" rx="15" ry="11" stroke="#d0d0d0" stroke-width="2" fill="none"/>
                <circle cx="50" cy="45" r="4.5" stroke="#d0d0d0" stroke-width="1.8" fill="none"/>
                <circle cx="50" cy="45" r="2" fill="#d0d0d0"/>
                <circle cx="47" cy="42" r="2.5" fill="#d0d0d0" opacity="0.25"/>
            </svg>
            <div class="text">OTOB</div>
        </div>
        <div class="header">
            <div>
                <h1>OTOB <span>Osint Tool Olimpov Bot</span></h1>
                <div class="sub">Запрос: {query} · Тип: {qtype} · {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</div>
            </div>
            <div><span class="badge badge-success">найдено: {total}</span></div>
        </div>
"""
    
    if all_results:
        for idx, item in enumerate(all_results[:25], 1):
            title = item.get('title', '—')[:60]
            text = item.get('text', '')[:200]
            extra = item.get('extra', '')
            
            details = []
            for key, value in item.items():
                if key not in ['title', 'text', 'extra'] and value:
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
        <div class="stats">📊 Найдено <strong>{total}</strong> результатов</div>
"""
    else:
        html += '<div class="empty">❌ Ничего не найдено</div>'
    
    html += f"""
        <div class="footer">🛡️ OTOB — Osint Tool Olimpov Bot · <a href="https://t.me/Osint_Tool_Olimpov_bot" target="_blank">@Osint_Tool_Olimpov_bot</a></div>
    </div>
</body>
</html>
"""
    return html

# ==================== МЕНЮ ====================

def main_menu_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🔍 Функции", callback_data="menu_functions")
    )
    markup.add(
        types.InlineKeyboardButton("👤 Профиль", callback_data="menu_profile"),
        types.InlineKeyboardButton("📊 Баланс", callback_data="menu_balance")
    )
    markup.add(
        types.InlineKeyboardButton("❓ Помощь", callback_data="menu_help"),
        types.InlineKeyboardButton("🧑‍💻 Разработчики", url="https://t.me/lkblyad")
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
        types.InlineKeyboardButton("👤 Username", callback_data="username_search")
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
                "👋 Привет! Я помогу тебе найти информацию в открытых источниках.\n\n"
                "📌 *Выбери действие:*",
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
                "• 🌐 Глобальный поиск — номер, email, ФИО, IP, домен\n\n"
                "📌 *Быстрый поиск:*\n"
                "• 📧 Email — проверка утечек\n"
                "• 📱 Телефон — оператор, регион\n"
                "• 👤 Username — поиск в соцсетях",
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
                f"👑 Админ: {'безлимитный' if user_id == ADMIN_ID else 'нет'}"
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
                "🧑‍💻 *Канал разработчиков:* @lkblyad",
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
                "🌐 *Глобальный поиск*\n\n"
                "Отправь запрос для поиска:\n"
                "• Номер: +79991234567\n"
                "• ФИО: Иванов Иван Иванович\n"
                "• Email: user@example.com\n"
                "• Никнейм: username\n"
                "• IP: 8.8.8.8\n"
                "• Домен: example.com\n"
                "• Любой текст\n\n"
                "ℹ️ 35+ OSINT-источников",
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
                "📧 *Проверка email*\n\n"
                "Отправь email для проверки утечек.\n\n"
                "Пример: user@example.com",
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
                "📱 *Проверка телефона*\n\n"
                "Отправь номер для проверки.\n\n"
                "Пример: +79991234567",
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
                "👤 *Поиск по username*\n\n"
                "Отправь никнейм для поиска в соцсетях.\n\n"
                "Пример: username",
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
            f"👋 Привет, {message.from_user.first_name}!\n\n"
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
        
        # Отправляем сообщение с приблизительным временем
        start_time = time.time()
        msg = bot.reply_to(
            message,
            "⏳ *Поиск по 35+ источникам...*\n"
            "⏱️ Приблизительное время: ~15-30 секунд",
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
                        f"⏱️ Время поиска: **{elapsed:.1f} сек**",
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
    bot.remove_webhook()
    bot.infinity_polling(timeout=60, long_polling_timeout=30)
