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
from datetime import datetime
from bs4 import BeautifulSoup
import telebot
from telebot import types
from http.server import HTTPServer, BaseHTTPRequestHandler
from fake_useragent import UserAgent
import phonenumbers
from phonenumbers import carrier, geocoder, timezone as phone_timezone
import pytz

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "8545020464"))
DB_PATH = os.path.join("/tmp", "otob_bot.db")
SEARCH_TIMEOUT = 120  # 2 минуты на весь поиск

# ===== КЛЮЧИ API (ОПЦИОНАЛЬНО) =====
VERIPHONE_KEY = os.environ.get("VERIPHONE_KEY")
OMKAR_KEY = os.environ.get("OMKAR_KEY")
NUMVERIFY_KEY = os.environ.get("NUMVERIFY_KEY")
ABSTRACT_API_KEY = os.environ.get("ABSTRACT_API_KEY")
BIGDATACLOUD_KEY = os.environ.get("BIGDATACLOUD_KEY")
HUNTER_KEY = os.environ.get("HUNTER_KEY")
SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY")

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

ua = UserAgent()

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

async def run_with_timeout(coro, timeout=12):
    """Запускает корутину с таймаутом (по умолчанию 12 секунд)"""
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        return None
    except Exception:
        return None

def request_via_scraperapi(url: str) -> str:
    if not SCRAPERAPI_KEY:
        return None
    try:
        import requests
        proxy_url = f"https://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={url}"
        response = requests.get(proxy_url, timeout=15)
        if response.status_code == 200:
            return response.text
    except:
        pass
    return None

def clean_phone(phone: str) -> str:
    """Очищает номер телефона от лишних символов"""
    return re.sub(r'\D', '', phone)

def _has_useful_data(item: dict) -> bool:
    """Проверяет, есть ли в словаре полезные данные"""
    if not item:
        return False
    
    # Если есть found: True — точно показываем
    if item.get('found') is True:
        return True
    
    # Если есть exists: True — показываем
    if item.get('exists') is True:
        return True
    
    # Если есть total > 0 — показываем
    if item.get('total', 0) > 0:
        return True
    
    # Проверяем наличие полезных полей
    useful_fields = ['name', 'names', 'carrier', 'operator', 'country', 'region', 
                     'status', 'spam_risk', 'password', 'hash', 'sources', 'breaches',
                     'country_code', 'timezone', 'valid', 'possible']
    
    for field in useful_fields:
        value = item.get(field)
        if value and value != '—' and value != '':
            return True
    
    # Проверяем title и text
    title = item.get('title', '')
    text = item.get('text', '')
    if title and title != '—' and title != '':
        return True
    if text and text != '—' and text != '':
        return True
    if item.get('extra') and item.get('extra') != '—':
        return True
    
    return False

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

# ==================== ВСЕ ФУНКЦИИ ПОИСКА (БЕЗ КЛЮЧЕЙ) ====================

# ----- 1. PHONENUMBERS -----
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

# ----- 2. WHATSAPP -----
async def whatsapp_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://wa.me/{clean}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10, allow_redirects=False) as resp:
                if resp.status == 200 or resp.status == 302:
                    return {"found": True, "exists": True, "url": f"https://wa.me/{clean}"}
                return {"found": False, "exists": False}
    except:
        return {"found": False, "exists": False}

# ----- 3. TELEGRAM -----
async def telegram_phone_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://t.me/+{clean}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10, allow_redirects=False) as resp:
                if resp.status == 200 or resp.status == 302:
                    return {"found": True, "exists": True, "url": f"https://t.me/+{clean}"}
                return {"found": False, "exists": False}
    except:
        return {"found": False, "exists": False}

# ----- 4. VIBER -----
async def viber_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://www.viber.com/{clean}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10, allow_redirects=False) as resp:
                return {"found": resp.status == 200, "exists": resp.status == 200}
    except:
        return {"found": False, "exists": False}

# ----- 5. TRUECALLER -----
async def truecaller_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://www.truecaller.com/search/{clean}"
        headers = {"User-Agent": ua.random}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, 'html5lib')
                    name = soup.find('div', class_=re.compile('name|title|fullname'))
                    if name:
                        return {"found": True, "name": name.get_text(strip=True)}
                return {"found": False}
    except:
        return {"found": False}

# ----- 6. GETCONTACT -----
async def getcontact_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://www.getcontact.com/ru/search/{clean}"
        headers = {"User-Agent": ua.random}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, 'html5lib')
                    names = soup.find_all('span', class_=re.compile('name|title'))
                    if names:
                        return {"found": True, "names": [n.get_text(strip=True) for n in names[:5]]}
                return {"found": False}
    except:
        return {"found": False}

# ----- 7. SPAMCALLS -----
async def spamcalls_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://spamcalls.net/ru/number/{clean}"
        headers = {"User-Agent": ua.random}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    if "спам" in html.lower() or "мошенник" in html.lower():
                        return {"found": True, "spam_risk": "high"}
                    elif "не спам" in html.lower():
                        return {"found": True, "spam_risk": "low"}
                return {"found": False, "spam_risk": "unknown"}
    except:
        return {"found": False, "spam_risk": "unknown"}

# ----- 8. GOOGLE DORKS -----
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
    ]
    for dork in dorks[:4]:
        try:
            url = f"https://html.duckduckgo.com/html/?q={dork.replace(' ', '+')}"
            headers = {"User-Agent": ua.random}
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=10) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        soup = BeautifulSoup(html, 'html5lib')
                        snippets = soup.select('.result__snippet')
                        for snippet in snippets[:2]:
                            text = snippet.get_text(strip=True)[:200]
                            if text and len(text) > 10:
                                results.append({"title": dork, "text": text, "found": True})
        except:
            continue
    return results

# ----- 9. LEAKCHECK -----
@safe_request
async def leakcheck_lookup(query: str) -> dict:
    url = f"https://leakcheck.io/api/public?check={query}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=10) as resp:
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

# ----- 10. HUDSON ROCK -----
@safe_request
async def hudsonrock_lookup(phone: str) -> dict:
    clean = clean_phone(phone)
    url = f"https://cavalier.hudsonrock.com/api/v1/search-by-username?username={clean}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get('total_results', 0) > 0:
                    return {
                        "found": True,
                        "total": data.get('total_results', 0),
                        "breaches": data.get('results', [])[:5]
                    }
    return {"found": False}

# ----- 11. HTMLWEB -----
@safe_request
async def htmlweb_lookup(phone: str) -> dict:
    clean = clean_phone(phone)
    url = f"https://htmlweb.ru/geo/api.php?json&telcod={clean}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data:
                    return {
                        "found": True,
                        "country": data.get('country', '—'),
                        "operator": data.get('operator', '—'),
                        "region": data.get('region', '—'),
                        "timezone": data.get('timezone', '—')
                    }
    return {"found": False}

# ----- 12. SMSC HLR -----
@safe_request
async def hlr_lookup(phone: str) -> dict:
    clean = clean_phone(phone)
    url = f"https://smsc.ru/testhlr.php?phone={clean}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.text()
                return {"found": True, "status": "✅ Активен" if 'OK' in data else "❌ Не активен"}
    return {"found": False}

# ----- 13. WHITEPAGES -----
async def whitepages_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://www.whitepages.com/phone/{clean}"
        headers = {"User-Agent": ua.random}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    if "No results found" not in html:
                        return {"found": True}
                return {"found": False}
    except:
        return {"found": False}

# ----- 14. FASTPEOPLESEARCH -----
async def fastpeoplesearch_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://www.fastpeoplesearch.com/phone/{clean}"
        headers = {"User-Agent": ua.random}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, 'html5lib')
                    name = soup.find('h1', class_=re.compile('name|title|fullname'))
                    if name:
                        return {"found": True, "name": name.get_text(strip=True)}
                return {"found": False}
    except:
        return {"found": False}

# ----- 15. THATSTHEM -----
async def thatsthem_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://thatsthem.com/phone/{clean}"
        headers = {"User-Agent": ua.random}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, 'html5lib')
                    name = soup.find('div', class_=re.compile('name|fullname'))
                    if name:
                        return {"found": True, "name": name.get_text(strip=True)}
                return {"found": False}
    except:
        return {"found": False}

# ----- 16. REVEALNAME -----
async def revealname_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://revealname.com/phone/{clean}"
        headers = {"User-Agent": ua.random}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, 'html5lib')
                    name = soup.find('div', class_=re.compile('name|owner|result'))
                    if name:
                        return {"found": True, "name": name.get_text(strip=True)}
                return {"found": False}
    except:
        return {"found": False}

# ----- 17. CALLERID -----
async def callerid_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://callerid.com/phone/{clean}"
        headers = {"User-Agent": ua.random}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, 'html5lib')
                    name = soup.find('h1', class_=re.compile('name|title'))
                    if name:
                        return {"found": True, "name": name.get_text(strip=True)}
                return {"found": False}
    except:
        return {"found": False}

# ----- 18. SPYDIALER -----
async def spydialer_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://www.spydialer.com/phone/{clean}"
        headers = {"User-Agent": ua.random}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    if "No records found" not in html:
                        return {"found": True}
                return {"found": False}
    except:
        return {"found": False}

# ----- 19. USPHONEBOOK -----
async def usphonebook_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://www.usphonebook.com/phone/{clean}"
        headers = {"User-Agent": ua.random}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, 'html5lib')
                    name = soup.find('div', class_=re.compile('name|fullname'))
                    if name:
                        return {"found": True, "name": name.get_text(strip=True)}
                return {"found": False}
    except:
        return {"found": False}

# ----- 20. SYNC.ME -----
async def syncme_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://sync.me/search?q={clean}"
        headers = {"User-Agent": ua.random}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    if "No results" not in html:
                        return {"found": True}
                return {"found": False}
    except:
        return {"found": False}

# ----- 21. WHOSENO -----
async def whoseno_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://whoseno.com/search?q={clean}"
        headers = {"User-Agent": ua.random}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, 'html5lib')
                    name = soup.find('div', class_=re.compile('name|result'))
                    if name:
                        return {"found": True, "name": name.get_text(strip=True)}
                return {"found": False}
    except:
        return {"found": False}

# ----- 22. DUCKDUCKGO (парсинг) -----
async def duckduckgo_search(query: str) -> list:
    url = f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}"
    selectors = {"result": ".result", "title": ".result__title a", "link": "a", "text": ".result__snippet"}
    return await parse_site(url, selectors, 5)

# ----- 23. SOCIALSEARCH -----
async def socialsearch_lookup(query: str) -> list:
    url = f"https://socialsearch.io/?q={query.replace(' ', '+')}"
    selectors = {"result": ".result, .profile, .card", "title": ".name, .title", "link": "a", "text": ".description", "extra": ".url, .handle"}
    return await parse_site(url, selectors, 5)

# ----- 24. PIPL -----
async def pipl_lookup(query: str) -> list:
    url = f"https://pipl.com/search/?q={query.replace(' ', '+')}"
    selectors = {"result": ".result, .person, .card", "title": ".name, .fullname", "link": "a", "text": ".bio, .description", "extra": ".location, .email, .phone, .social"}
    return await parse_site(url, selectors, 5)

# ----- 25. X-RAY -----
async def xray_lookup(query: str) -> list:
    url = f"https://x-ray.contact/search?q={query}"
    selectors = {"result": ".result-item, .social-link, .profile-item", "title": ".title, .name", "link": "a", "text": ".description", "extra": ".extra"}
    return await parse_site(url, selectors, 5)

# ----- 26. IDCRAWL -----
async def idcrawl_lookup(query: str) -> list:
    url = f"https://idcrawl.com/{query}"
    selectors = {"result": ".result-item, .profile-item", "title": ".title, .name", "link": "a", "text": ".description", "extra": ".extra"}
    return await parse_site(url, selectors, 5)

# ----- 27. TRUEPEOPLESEARCH -----
async def truepeoplesearch_lookup(query: str) -> list:
    if re.search(r'\d', query):
        url = f"https://truepeoplesearch.com/results?phoneno={query}"
    else:
        url = f"https://truepeoplesearch.com/results?name={query.replace(' ', '+')}"
    selectors = {"result": ".card, .person-item", "title": ".name, .title", "text": ".description", "extra": ".address, .phone, .relatives"}
    return await parse_site(url, selectors, 5)

# ----- 28. FASTPEOPLESEARCH (парсинг) -----
async def fastpeoplesearch_parse(phone: str) -> list:
    clean = clean_phone(phone)
    url = f"https://www.fastpeoplesearch.com/phone/{clean}"
    selectors = {"result": ".result, .person-item", "title": ".name, .title", "text": ".description", "extra": ".address"}
    return await parse_site(url, selectors, 5)

# ----- 29. THATSTHEM (парсинг) -----
async def thatsthem_lookup(phone: str) -> list:
    clean = clean_phone(phone)
    url = f"https://thatsthem.com/phone/{clean}"
    selectors = {"result": ".result, .person, .card", "title": ".name, .fullname", "text": ".address, .location", "extra": ".age, .relatives, .phone"}
    return await parse_site(url, selectors, 5)

# ==================== ПАРСЕР (ОСНОВНОЙ) ====================

async def parse_site(url: str, selectors: dict, max_results: int = 5) -> list:
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
        html = None
        if SCRAPERAPI_KEY:
            html = request_via_scraperapi(url)
        if not html:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=15) as resp:
                    if resp.status == 200:
                        html = await resp.text()
        if html:
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

# ==================== ДОПОЛНИТЕЛЬНЫЕ ФУНКЦИИ ====================

@safe_request
async def ipinfo_lookup(ip: str) -> dict:
    url = f"https://ipinfo.io/{ip}/json"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=10) as resp:
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

@safe_request
async def ip_api_lookup(ip: str) -> dict:
    url = f"http://ip-api.com/json/{ip}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=10) as resp:
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

async def crtsh_lookup(domain: str) -> list:
    url = f"https://crt.sh/?q={domain}&output=json"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data:
                    return [{"domain": item.get('name_value', '—')} for item in data[:5]]
    return []

async def whois_lookup(domain: str) -> dict:
    url = f"https://api.whois.vu/?q={domain}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                return {
                    "found": True,
                    "registrar": data.get('registrar', '—'),
                    "creation_date": data.get('creation_date', '—'),
                    "expiration_date": data.get('expiration_date', '—')
                }
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
            ("phonenumbers", run_with_timeout(phonenumbers_info(query), 12)),
            ("whatsapp", run_with_timeout(whatsapp_check(query), 10)),
            ("telegram", run_with_timeout(telegram_phone_check(query), 10)),
            ("viber", run_with_timeout(viber_check(query), 10)),
            ("truecaller", run_with_timeout(truecaller_check(query), 12)),
            ("getcontact", run_with_timeout(getcontact_check(query), 12)),
            ("spamcalls", run_with_timeout(spamcalls_check(query), 10)),
            ("google_dorks", run_with_timeout(google_dorks_search(query), 15)),
            ("leakcheck", run_with_timeout(leakcheck_lookup(query), 12)),
            ("hudsonrock", run_with_timeout(hudsonrock_lookup(query), 12)),
            ("htmlweb", run_with_timeout(htmlweb_lookup(query), 10)),
            ("hlr", run_with_timeout(hlr_lookup(query), 10)),
            ("whitepages", run_with_timeout(whitepages_check(query), 12)),
            ("fastpeoplesearch", run_with_timeout(fastpeoplesearch_check(query), 12)),
            ("thatsthem", run_with_timeout(thatsthem_check(query), 12)),
            ("revealname", run_with_timeout(revealname_check(query), 12)),
            ("callerid", run_with_timeout(callerid_check(query), 12)),
            ("spydialer", run_with_timeout(spydialer_check(query), 12)),
            ("usphonebook", run_with_timeout(usphonebook_check(query), 12)),
            ("syncme", run_with_timeout(syncme_check(query), 12)),
            ("whoseno", run_with_timeout(whoseno_check(query), 12)),
            ("duckduckgo", run_with_timeout(duckduckgo_search(query), 15)),
            ("socialsearch", run_with_timeout(socialsearch_lookup(query), 12)),
            ("pipl", run_with_timeout(pipl_lookup(query), 12)),
            ("xray", run_with_timeout(xray_lookup(query), 12)),
            ("idcrawl", run_with_timeout(idcrawl_lookup(query), 12)),
            ("truepeoplesearch", run_with_timeout(truepeoplesearch_lookup(query), 12)),
            ("fastpeoplesearch_parse", run_with_timeout(fastpeoplesearch_parse(query), 12)),
            ("thatsthem_parse", run_with_timeout(thatsthem_lookup(query), 12)),
        ]
    
    elif qtype == "email":
        tasks = [
            ("leakcheck", run_with_timeout(leakcheck_lookup(query), 12)),
            ("hudsonrock", run_with_timeout(hudsonrock_lookup(query), 12)),
            ("socialsearch", run_with_timeout(socialsearch_lookup(query), 12)),
            ("pipl", run_with_timeout(pipl_lookup(query), 12)),
            ("duckduckgo", run_with_timeout(duckduckgo_search(query), 15)),
        ]
    
    elif qtype == "username":
        tasks = [
            ("xray", run_with_timeout(xray_lookup(query), 12)),
            ("idcrawl", run_with_timeout(idcrawl_lookup(query), 12)),
            ("socialsearch", run_with_timeout(socialsearch_lookup(query), 12)),
            ("pipl", run_with_timeout(pipl_lookup(query), 12)),
            ("duckduckgo", run_with_timeout(duckduckgo_search(query), 15)),
            ("leakcheck", run_with_timeout(leakcheck_lookup(query), 12)),
        ]
    
    elif qtype == "ip":
        tasks = [
            ("ipinfo", run_with_timeout(ipinfo_lookup(query), 10)),
            ("ip_api", run_with_timeout(ip_api_lookup(query), 10)),
            ("duckduckgo", run_with_timeout(duckduckgo_search(query), 15)),
        ]
    
    elif qtype == "domain":
        tasks = [
            ("crtsh", run_with_timeout(crtsh_lookup(query), 10)),
            ("whois", run_with_timeout(whois_lookup(query), 10)),
            ("duckduckgo", run_with_timeout(duckduckgo_search(query), 15)),
        ]
    else:
        tasks = [("duckduckgo", run_with_timeout(duckduckgo_search(query), 15))]
    
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

# ==================== ГЕНЕРАЦИЯ HTML-ОТЧЁТА (ИСПРАВЛЕННАЯ) ====================

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
                    if _has_useful_data(item):
                        item['_source'] = source_name
                        all_results.append(item)
                else:
                    if str(item) and str(item) != '—' and str(item) != '':
                        all_results.append({
                            "title": str(item),
                            "_source": source_name,
                            "text": "",
                            "extra": ""
                        })
        elif isinstance(items, dict):
            if _has_useful_data(items):
                item_copy = items.copy()
                item_copy['_source'] = source_name
                all_results.append(item_copy)
        else:
            if str(items) and str(items) != '—' and str(items) != '':
                all_results.append({
                    "title": str(items),
                    "_source": source_name,
                    "text": "",
                    "extra": ""
                })
    
    # Убираем дубликаты
    seen_titles = set()
    unique_results = []
    for item in all_results:
        title_key = str(item.get('title', ''))[:50] + str(item.get('_source', ''))
        if title_key not in seen_titles:
            seen_titles.add(title_key)
            unique_results.append(item)
    all_results = unique_results
    
    # Показываем ВСЕ результаты, которые нашли что-то (БЕЗ ОГРАНИЧЕНИЯ)
    display_results = all_results
    
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
            box-shadow: 0 0 40px rgba(0,255,0,0.03);
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
        }}
        .watermark .text {{
            color: #00ff00;
            font-size: 14px;
            font-weight: 900;
            letter-spacing: 4px;
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
        .source-tag {{
            display: inline-block;
            background: #1a2a1a;
            color: #4a8a4a;
            font-size: 10px;
            padding: 2px 10px;
            border-radius: 4px;
            margin-left: 10px;
            border: 1px solid #1a3a1a;
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
        .scanline {{
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            pointer-events: none;
            background: repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,255,0,0.003) 2px, rgba(0,255,0,0.003) 4px);
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
            <div class="text">🔍 OTOB</div>
        </div>
        <div class="header">
            <div>
                <h1>🔍 OTOB <span>OSINT</span></h1>
                <div class="sub">⚡ Запрос: {query} · Тип: {qtype} · {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</div>
                <div class="sub" style="color:#2a5a2a; margin-top:2px;">🛡️ 80+ источников · Глобальный поиск</div>
            </div>
            <div><span class="badge badge-success">🎯 НАЙДЕНО: {total}</span></div>
        </div>
        <div class="stats-bar">
            <span class="stat">📊 Всего результатов: <strong>{total}</strong></span>
            <span class="stat">🔍 Источников с данными: <strong>{len(display_results)}</strong></span>
            <span class="stat">⚡ Статус: <strong style="color:#00ff00;">АКТИВЕН</strong></span>
        </div>
"""
    
    if display_results:
        # Показываем ВСЕ результаты (БЕЗ ОГРАНИЧЕНИЯ!)
        for idx, item in enumerate(display_results, 1):
            source = item.get('_source', '')
            title = item.get('title', '—')
            if isinstance(title, bool):
                title = "✅ Да" if title else "❌ Нет"
            elif title == '' or title == '—':
                title = '—'
            title = str(title)[:80]
            
            text = item.get('text', '')
            if isinstance(text, bool):
                text = "✅ Да" if text else "❌ Нет"
            elif text == '' or text == '—':
                text = ''
            text = str(text)[:250]
            
            extra = item.get('extra', '')
            if isinstance(extra, bool):
                extra = "✅ Да" if extra else "❌ Нет"
            elif extra == '' or extra == '—':
                extra = ''
            extra = str(extra)
            
            link = item.get('link', '')
            
            # Собираем дополнительные поля
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
            
            # Пропускаем только если совсем ничего нет
            if not details and not text and not extra and (title == '—' or title == ''):
                continue
            
            html += f"""
        <div class="result-item">
            <div class="title">
                <span class="index">#{idx}</span>
                {title}
                {f'<span class="source-tag">{source[:15]}</span>' if source else ''}
                {f'<a href="{link}" target="_blank">🔗</a>' if link else ''}
            </div>
"""
            if text:
                html += f"            <div class=\"text\">{text}</div>\n"
            if extra:
                html += f"            <div class=\"extra\">📎 {extra}</div>\n"
            if details:
                for detail in details[:6]:
                    html += f"            <div class=\"extra\">• {detail}</div>\n"
            html += "        </div>\n"
        
        html += f"""
        <div style="text-align:center; margin-top:20px; padding:12px; border:1px solid #1a2a1a; border-radius:8px; color:#4a6a4a; font-size:13px;">
            📊 Показано {len(display_results)} из {total} найденных результатов
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

# ==================== ОБРАБОТЧИКИ ====================

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    try:
        bot.answer_callback_query(call.id)
        
        if call.data == "menu_back":
            bot.edit_message_text(
                "🔍 *OTOB — Osint Tool Olimpov Bot*\n\n"
                "🕵️ *Глобальный OSINT-поиск*\n"
                "80+ источников · Мгновенный отчёт\n\n"
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
                "⚡ *80+ OSINT-источников*\n"
                "🕵️ Имя · Адрес · Оператор · Соцсети · Утечки · Даркнет\n"
                "📱 WhatsApp · Telegram · Viber · Truecaller · GetContact",
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
                "🔍 Проверка в базах утечек (HIBP, LeakCheck, Hudson Rock)\n"
                "📊 Репутация (EmailRep)\n"
                "🏢 Компания (Clearbit, Hunter)",
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
                "📱 WhatsApp · Telegram · Viber\n"
                "⚡ 40+ источников по номеру + утечки",
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
                "🕵️ Pipl · PeekYou · SocialSearch\n"
                "💀 Утечки · Даркнет",
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
                "🔍 Геолокация · WHOIS · SSL-сертификаты\n"
                "💀 Shodan · Censys · VirusTotal",
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
            "⏳ *Глобальный поиск по 80+ источникам...*\n"
            "⏱️ Время: до 2 минут\n"
            "🕵️ Идёт сканирование...",
            parse_mode="Markdown"
        )
        
        # ======== ЗАПУСК С ТАЙМАУТОМ 120 СЕКУНД ========
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                data = loop.run_until_complete(
                    asyncio.wait_for(global_lookup(text), timeout=120)
                )
            except asyncio.TimeoutError:
                bot.edit_message_text(
                    "⚠️ *Поиск прерван по таймауту (2 минуты)*\n\n"
                    "📌 Показаны только быстрые результаты.\n"
                    "🔄 Попробуй повторить запрос позже.",
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
        # =============================================
        
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
    logger.info("⚡ Таймаут поиска: 2 минуты")
    logger.info("📱 Добавлены: WhatsApp, Telegram, Viber, Truecaller, GetContact")
    logger.info("💀 Добавлены: LeakCheck, HudsonRock, Google Dorks")
    logger.info("🔍 Добавлены: Whitepages, FastPeopleSearch, ThatsThem, RevealName, CallerID, SpyDialer, USPhoneBook, Sync.me, WhoSeNo")
    
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
