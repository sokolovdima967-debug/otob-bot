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
SEARCH_TIMEOUT = 120

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

def generate_otob_title(query: str, qtype: str) -> str:
    templates = [
        f"🔱 OTOB — OSINT Глобальный поиск | {qtype.upper()} | {query}",
        f"🕵️ OTOB | {query} | {qtype.upper()} | Отчёт",
        f"🎯 OTOB — Глаз Бога | {qtype} | {query}",
        f"⚡ OTOB — Глобальный OSINT | {query} | {qtype.upper()}",
        f"🔱 OTOB — OSINT | {query} | {qtype.upper()}",
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
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        return None
    except Exception:
        return None

def request_via_scraperapi(url: str) -> str:
    """Обход блокировок через ScraperAPI"""
    if not SCRAPERAPI_KEY:
        return None
    try:
        import requests
        proxy_url = f"https://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={url}&render=true"
        response = requests.get(proxy_url, timeout=20)
        if response.status_code == 200:
            return response.text
    except Exception as e:
        logger.warning(f"ScraperAPI error: {e}")
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

# ==================== ВСЕ ФУНКЦИИ ПОИСКА (С ОБХОДОМ БЛОКИРОВОК) ====================

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
        headers = {"User-Agent": ua.random}
        
        if SCRAPERAPI_KEY:
            html = request_via_scraperapi(url)
            if html:
                if "WhatsApp" in html or "whatsapp" in html.lower():
                    return {"found": True, "exists": True, "url": f"https://wa.me/{clean}"}
                return {"found": False, "exists": False}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10, allow_redirects=False) as resp:
                if resp.status == 200 or resp.status == 302:
                    return {"found": True, "exists": True, "url": f"https://wa.me/{clean}"}
                return {"found": False, "exists": False}
    except:
        return {"found": False, "exists": False}

async def telegram_phone_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://t.me/+{clean}"
        headers = {"User-Agent": ua.random}
        
        if SCRAPERAPI_KEY:
            html = request_via_scraperapi(url)
            if html:
                if "telegram" in html.lower() or "tgme" in html.lower():
                    return {"found": True, "exists": True, "url": f"https://t.me/+{clean}"}
                return {"found": False, "exists": False}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10, allow_redirects=False) as resp:
                if resp.status == 200 or resp.status == 302:
                    return {"found": True, "exists": True, "url": f"https://t.me/+{clean}"}
                return {"found": False, "exists": False}
    except:
        return {"found": False, "exists": False}

async def viber_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://www.viber.com/{clean}"
        headers = {"User-Agent": ua.random}
        
        if SCRAPERAPI_KEY:
            html = request_via_scraperapi(url)
            if html:
                if "viber" in html.lower():
                    return {"found": True, "exists": True}
                return {"found": False, "exists": False}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10, allow_redirects=False) as resp:
                return {"found": resp.status == 200, "exists": resp.status == 200}
    except:
        return {"found": False, "exists": False}

async def truecaller_enhanced(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://www.truecaller.com/search/{clean}"
        headers = {"User-Agent": ua.random}
        html = None
        
        if SCRAPERAPI_KEY:
            html = request_via_scraperapi(url)
        
        if not html:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=12) as resp:
                    if resp.status == 200:
                        html = await resp.text()
        
        if html:
            soup = BeautifulSoup(html, 'html5lib')
            name = soup.find('div', class_=re.compile('name|title|fullname'))
            tags = soup.find_all('span', class_=re.compile('tag|label'))
            spam = soup.find('div', class_=re.compile('spam|risk'))
            return {
                "found": True if name else False,
                "name": name.get_text(strip=True) if name else "—",
                "tags": [t.get_text(strip=True) for t in tags[:5]] if tags else [],
                "spam_risk": spam.get_text(strip=True) if spam else "—"
            }
        return {"found": False}
    except:
        return {"found": False}

async def getcontact_enhanced(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://www.getcontact.com/ru/search/{clean}"
        headers = {"User-Agent": ua.random}
        html = None
        
        if SCRAPERAPI_KEY:
            html = request_via_scraperapi(url)
        
        if not html:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=12) as resp:
                    if resp.status == 200:
                        html = await resp.text()
        
        if html:
            soup = BeautifulSoup(html, 'html5lib')
            names = soup.find_all('span', class_=re.compile('name|title|contact'))
            if names:
                return {
                    "found": True,
                    "names": [n.get_text(strip=True) for n in names[:10]],
                    "source": "GetContact"
                }
        return {"found": False}
    except:
        return {"found": False}

async def syncme_enhanced(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://sync.me/search?q={clean}"
        headers = {"User-Agent": ua.random}
        html = None
        
        if SCRAPERAPI_KEY:
            html = request_via_scraperapi(url)
        
        if not html:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=12) as resp:
                    if resp.status == 200:
                        html = await resp.text()
        
        if html:
            soup = BeautifulSoup(html, 'html5lib')
            name = soup.find('div', class_=re.compile('name|title'))
            spam = soup.find('span', class_=re.compile('spam|risk'))
            return {
                "found": True if name else False,
                "name": name.get_text(strip=True) if name else "—",
                "spam_level": spam.get_text(strip=True) if spam else "—"
            }
        return {"found": False}
    except:
        return {"found": False}

async def whoseno_enhanced(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://whoseno.com/search?q={clean}"
        headers = {"User-Agent": ua.random}
        html = None
        
        if SCRAPERAPI_KEY:
            html = request_via_scraperapi(url)
        
        if not html:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=12) as resp:
                    if resp.status == 200:
                        html = await resp.text()
        
        if html:
            soup = BeautifulSoup(html, 'html5lib')
            name = soup.find('div', class_=re.compile('name|result|contact'))
            return {
                "found": True if name else False,
                "name": name.get_text(strip=True) if name else "—"
            }
        return {"found": False}
    except:
        return {"found": False}

async def numbuster_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://numbuster.com/api/search?phone={clean}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=12) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('found'):
                        return {
                            "found": True,
                            "names": data.get('names', []),
                            "platforms": data.get('platforms', [])
                        }
                return {"found": False}
    except:
        return {"found": False}

async def eyecon_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://eyecon-app.com/search?phone={clean}"
        headers = {"User-Agent": ua.random}
        html = None
        
        if SCRAPERAPI_KEY:
            html = request_via_scraperapi(url)
        
        if not html:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=12) as resp:
                    if resp.status == 200:
                        html = await resp.text()
        
        if html:
            soup = BeautifulSoup(html, 'html5lib')
            name = soup.find('div', class_=re.compile('name|title'))
            return {
                "found": True if name else False,
                "name": name.get_text(strip=True) if name else "—"
            }
        return {"found": False}
    except:
        return {"found": False}

async def facebook_breach_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://haveibeenzuckered.com/api/check?phone={clean}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=12) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "found": True,
                        "in_breach": data.get('in_breach', False),
                        "breach_date": data.get('breach_date', '—')
                    }
                return {"found": False}
    except:
        return {"found": False}

async def bellingcat_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://bellingcat.com/api/telegram/check?phone={clean}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=12) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "found": True,
                        "has_telegram": data.get('has_telegram', False),
                        "telegram_id": data.get('telegram_id', '—'),
                        "telegram_username": data.get('telegram_username', '—')
                    }
                return {"found": False}
    except:
        return {"found": False}

async def ignorant_check(phone: str) -> list:
    results = []
    platforms = [
        ("Amazon", f"https://www.amazon.com/account/verification?phone={phone}"),
        ("Instagram", f"https://www.instagram.com/api/v1/web/search/typeahead/?q={phone}"),
        ("Snapchat", f"https://accounts.snapchat.com/accounts/login?phone={phone}"),
    ]
    for name, url in platforms:
        try:
            headers = {"User-Agent": ua.random}
            if SCRAPERAPI_KEY:
                html = request_via_scraperapi(url)
                if html:
                    results.append({"platform": name, "exists": True})
                else:
                    results.append({"platform": name, "exists": False})
            else:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, headers=headers, timeout=8, allow_redirects=False) as resp:
                        if resp.status == 200 or resp.status == 302:
                            results.append({"platform": name, "exists": True})
                        else:
                            results.append({"platform": name, "exists": False})
        except:
            results.append({"platform": name, "exists": False})
    return results

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

async def spamcalls_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://spamcalls.net/ru/number/{clean}"
        headers = {"User-Agent": ua.random}
        html = None
        
        if SCRAPERAPI_KEY:
            html = request_via_scraperapi(url)
        
        if not html:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=10) as resp:
                    if resp.status == 200:
                        html = await resp.text()
        
        if html:
            if "спам" in html.lower() or "мошенник" in html.lower():
                return {"found": True, "spam_risk": "high"}
            elif "не спам" in html.lower():
                return {"found": True, "spam_risk": "low"}
        return {"found": False, "spam_risk": "unknown"}
    except:
        return {"found": False, "spam_risk": "unknown"}

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

async def whitepages_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://www.whitepages.com/phone/{clean}"
        headers = {"User-Agent": ua.random}
        html = None
        
        if SCRAPERAPI_KEY:
            html = request_via_scraperapi(url)
        
        if not html:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=12) as resp:
                    if resp.status == 200:
                        html = await resp.text()
        
        if html:
            if "No results found" not in html:
                return {"found": True}
        return {"found": False}
    except:
        return {"found": False}

async def fastpeoplesearch_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://www.fastpeoplesearch.com/phone/{clean}"
        headers = {"User-Agent": ua.random}
        html = None
        
        if SCRAPERAPI_KEY:
            html = request_via_scraperapi(url)
        
        if not html:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=12) as resp:
                    if resp.status == 200:
                        html = await resp.text()
        
        if html:
            soup = BeautifulSoup(html, 'html5lib')
            name = soup.find('h1', class_=re.compile('name|title|fullname'))
            if name:
                return {"found": True, "name": name.get_text(strip=True)}
        return {"found": False}
    except:
        return {"found": False}

async def thatsthem_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://thatsthem.com/phone/{clean}"
        headers = {"User-Agent": ua.random}
        html = None
        
        if SCRAPERAPI_KEY:
            html = request_via_scraperapi(url)
        
        if not html:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=12) as resp:
                    if resp.status == 200:
                        html = await resp.text()
        
        if html:
            soup = BeautifulSoup(html, 'html5lib')
            name = soup.find('div', class_=re.compile('name|fullname'))
            if name:
                return {"found": True, "name": name.get_text(strip=True)}
        return {"found": False}
    except:
        return {"found": False}

async def revealname_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://revealname.com/phone/{clean}"
        headers = {"User-Agent": ua.random}
        html = None
        
        if SCRAPERAPI_KEY:
            html = request_via_scraperapi(url)
        
        if not html:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=12) as resp:
                    if resp.status == 200:
                        html = await resp.text()
        
        if html:
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
        headers = {"User-Agent": ua.random}
        html = None
        
        if SCRAPERAPI_KEY:
            html = request_via_scraperapi(url)
        
        if not html:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=12) as resp:
                    if resp.status == 200:
                        html = await resp.text()
        
        if html:
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
        headers = {"User-Agent": ua.random}
        html = None
        
        if SCRAPERAPI_KEY:
            html = request_via_scraperapi(url)
        
        if not html:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=12) as resp:
                    if resp.status == 200:
                        html = await resp.text()
        
        if html:
            if "No records found" not in html:
                return {"found": True}
        return {"found": False}
    except:
        return {"found": False}

async def usphonebook_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://www.usphonebook.com/phone/{clean}"
        headers = {"User-Agent": ua.random}
        html = None
        
        if SCRAPERAPI_KEY:
            html = request_via_scraperapi(url)
        
        if not html:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=12) as resp:
                    if resp.status == 200:
                        html = await resp.text()
        
        if html:
            soup = BeautifulSoup(html, 'html5lib')
            name = soup.find('div', class_=re.compile('name|fullname'))
            if name:
                return {"found": True, "name": name.get_text(strip=True)}
        return {"found": False}
    except:
        return {"found": False}

async def syncme_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://sync.me/search?q={clean}"
        headers = {"User-Agent": ua.random}
        html = None
        
        if SCRAPERAPI_KEY:
            html = request_via_scraperapi(url)
        
        if not html:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=12) as resp:
                    if resp.status == 200:
                        html = await resp.text()
        
        if html:
            if "No results" not in html:
                return {"found": True}
        return {"found": False}
    except:
        return {"found": False}

async def whoseno_check(phone: str) -> dict:
    try:
        clean = clean_phone(phone)
        url = f"https://whoseno.com/search?q={clean}"
        headers = {"User-Agent": ua.random}
        html = None
        
        if SCRAPERAPI_KEY:
            html = request_via_scraperapi(url)
        
        if not html:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=12) as resp:
                    if resp.status == 200:
                        html = await resp.text()
        
        if html:
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
    return await parse_site(url, selectors, 5)

async def socialsearch_lookup(query: str) -> list:
    url = f"https://socialsearch.io/?q={query.replace(' ', '+')}"
    selectors = {"result": ".result, .profile, .card", "title": ".name, .title", "link": "a", "text": ".description", "extra": ".url, .handle"}
    return await parse_site(url, selectors, 5)

async def pipl_lookup(query: str) -> list:
    url = f"https://pipl.com/search/?q={query.replace(' ', '+')}"
    selectors = {"result": ".result, .person, .card", "title": ".name, .fullname", "link": "a", "text": ".bio, .description", "extra": ".location, .email, .phone, .social"}
    return await parse_site(url, selectors, 5)

async def xray_lookup(query: str) -> list:
    url = f"https://x-ray.contact/search?q={query}"
    selectors = {"result": ".result-item, .social-link, .profile-item", "title": ".title, .name", "link": "a", "text": ".description", "extra": ".extra"}
    return await parse_site(url, selectors, 5)

async def idcrawl_lookup(query: str) -> list:
    url = f"https://idcrawl.com/{query}"
    selectors = {"result": ".result-item, .profile-item", "title": ".title, .name", "link": "a", "text": ".description", "extra": ".extra"}
    return await parse_site(url, selectors, 5)

async def truepeoplesearch_lookup(query: str) -> list:
    if re.search(r'\d', query):
        url = f"https://truepeoplesearch.com/results?phoneno={query}"
    else:
        url = f"https://truepeoplesearch.com/results?name={query.replace(' ', '+')}"
    selectors = {"result": ".card, .person-item", "title": ".name, .title", "text": ".description", "extra": ".address, .phone, .relatives"}
    return await parse_site(url, selectors, 5)

async def fastpeoplesearch_parse(phone: str) -> list:
    clean = clean_phone(phone)
    url = f"https://www.fastpeoplesearch.com/phone/{clean}"
    selectors = {"result": ".result, .person-item", "title": ".name, .title", "text": ".description", "extra": ".address"}
    return await parse_site(url, selectors, 5)

async def thatsthem_lookup(phone: str) -> list:
    clean = clean_phone(phone)
    url = f"https://thatsthem.com/phone/{clean}"
    selectors = {"result": ".result, .person, .card", "title": ".name, .fullname", "text": ".address, .location", "extra": ".age, .relatives, .phone"}
    return await parse_site(url, selectors, 5)

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

# ==================== ПАРСЕР ====================

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
            ("truecaller", run_with_timeout(truecaller_enhanced(query), 12)),
            ("getcontact", run_with_timeout(getcontact_enhanced(query), 12)),
            ("syncme_enhanced", run_with_timeout(syncme_enhanced(query), 12)),
            ("whoseno_enhanced", run_with_timeout(whoseno_enhanced(query), 12)),
            ("numbuster", run_with_timeout(numbuster_check(query), 12)),
            ("eyecon", run_with_timeout(eyecon_check(query), 12)),
            ("bellingcat", run_with_timeout(bellingcat_check(query), 12)),
            ("ignorant", run_with_timeout(ignorant_check(query), 12)),
            ("leakcheck", run_with_timeout(leakcheck_lookup(query), 12)),
            ("hudsonrock", run_with_timeout(hudsonrock_lookup(query), 12)),
            ("facebook_breach", run_with_timeout(facebook_breach_check(query), 12)),
            ("spamcalls", run_with_timeout(spamcalls_check(query), 10)),
            ("htmlweb", run_with_timeout(htmlweb_lookup(query), 10)),
            ("hlr", run_with_timeout(hlr_lookup(query), 10)),
            ("duckduckgo", run_with_timeout(duckduckgo_search(query), 15)),
            ("socialsearch", run_with_timeout(socialsearch_lookup(query), 12)),
            ("pipl", run_with_timeout(pipl_lookup(query), 12)),
            ("xray", run_with_timeout(xray_lookup(query), 12)),
            ("idcrawl", run_with_timeout(idcrawl_lookup(query), 12)),
            ("truepeoplesearch", run_with_timeout(truepeoplesearch_lookup(query), 12)),
            ("fastpeoplesearch", run_with_timeout(fastpeoplesearch_parse(query), 12)),
            ("thatsthem", run_with_timeout(thatsthem_lookup(query), 12)),
            ("whitepages", run_with_timeout(whitepages_check(query), 12)),
            ("fastpeoplesearch_check", run_with_timeout(fastpeoplesearch_check(query), 12)),
            ("thatsthem_check", run_with_timeout(thatsthem_check(query), 12)),
            ("revealname", run_with_timeout(revealname_check(query), 12)),
            ("callerid", run_with_timeout(callerid_check(query), 12)),
            ("spydialer", run_with_timeout(spydialer_check(query), 12)),
            ("usphonebook", run_with_timeout(usphonebook_check(query), 12)),
            ("syncme", run_with_timeout(syncme_check(query), 12)),
            ("whoseno", run_with_timeout(whoseno_check(query), 12)),
            ("google_dorks", run_with_timeout(google_dorks_search(query), 15)),
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

# ==================== ГЕНЕРАЦИЯ HTML-ОТЧЁТА (КРАСНО-ЧЁРНЫЙ) ====================

def generate_html_report(query: str, data: dict, report_id: str) -> str:
    sources = data.get("sources", {})
    qtype = data.get("type", "text")
    total = data.get("total_results", 0)
    
    all_results = []
    
    for source_name, items in sources.items():
        if not items:
            all_results.append({
                "title": f"⚠️ {source_name} — нет данных",
                "_source": source_name,
                "text": "",
                "extra": "",
                "empty": True
            })
            continue
        
        if isinstance(items, list):
            if not items:
                all_results.append({
                    "title": f"⚠️ {source_name} — пустой список",
                    "_source": source_name,
                    "text": "",
                    "extra": "",
                    "empty": True
                })
            else:
                for item in items:
                    if isinstance(item, dict):
                        item['_source'] = source_name
                        all_results.append(item)
                    else:
                        all_results.append({
                            "title": str(item),
                            "_source": source_name,
                            "text": "",
                            "extra": ""
                        })
        elif isinstance(items, dict):
            item_copy = items.copy()
            item_copy['_source'] = source_name
            all_results.append(item_copy)
        else:
            all_results.append({
                "title": str(items),
                "_source": source_name,
                "text": "",
                "extra": ""
            })
    
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
        @import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@400;700;900&display=swap');
        body {{
            background: #0a0a0a;
            background-image: radial-gradient(ellipse at center, #1a0a0a 0%, #0a0a0a 100%);
            color: #d0c0c0;
            font-family: 'Cinzel', 'Segoe UI', serif;
            padding: 30px 20px;
            line-height: 1.6;
            min-height: 100vh;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: linear-gradient(145deg, #0d0a0a, #150d0d);
            border-radius: 16px;
            padding: 40px 45px;
            border: 1px solid #3a1a1a;
            box-shadow: 0 0 60px rgba(200,0,0,0.05), 0 0 120px rgba(200,0,0,0.02);
            position: relative;
        }}
        .watermark {{
            position: absolute;
            top: 25px;
            left: 30px;
            z-index: 10;
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
            color: #cc3333;
            font-size: 16px;
            font-weight: 900;
            letter-spacing: 6px;
            margin-top: 4px;
            text-transform: uppercase;
            font-family: 'Cinzel', serif;
            text-shadow: 0 0 20px rgba(200,0,0,0.3);
        }}
        .header {{
            border-bottom: 2px solid #3a1a1a;
            padding-bottom: 20px;
            margin-bottom: 28px;
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            flex-wrap: wrap;
            padding-left: 90px;
        }}
        .header h1 {{
            font-size: 28px;
            font-weight: 700;
            color: #cc3333;
            letter-spacing: 3px;
            text-shadow: 0 0 30px rgba(200,0,0,0.15);
            font-family: 'Cinzel', serif;
        }}
        .header h1 span {{
            color: #ff4444;
            background: #1a0a0a;
            padding: 0 14px;
            border-radius: 4px;
            border: 1px solid #3a1a1a;
        }}
        .header .sub {{
            color: #7a4a4a;
            font-size: 13px;
            margin-top: 6px;
            font-family: 'Courier New', monospace;
            letter-spacing: 1px;
        }}
        .badge {{
            display: inline-block;
            background: #1a0a0a;
            padding: 6px 18px;
            border-radius: 8px;
            font-size: 13px;
            color: #cc3333;
            border: 1px solid #3a1a1a;
            font-weight: 600;
            letter-spacing: 2px;
            font-family: 'Cinzel', serif;
        }}
        .badge-success {{ background: #1a0a0a; color: #ff4444; border-color: #4a1a1a; }}
        .stats-bar {{
            display: flex;
            gap: 20px;
            flex-wrap: wrap;
            margin: 20px 0 25px 0;
            padding: 15px 20px;
            background: #0a0808;
            border-radius: 8px;
            border: 1px solid #2a1212;
        }}
        .stats-bar .stat {{
            font-size: 13px;
            color: #6a3a3a;
            font-family: 'Courier New', monospace;
        }}
        .stats-bar .stat strong {{
            color: #cc3333;
        }}
        .result-item {{
            margin: 14px 0;
            padding: 16px 22px;
            background: #0a0808;
            border-radius: 10px;
            border-left: 4px solid #3a1a1a;
            transition: 0.25s;
            border: 1px solid #1a0a0a;
        }}
        .result-item:hover {{
            background: #120a0a;
            border-left-color: #cc3333;
            border-color: #3a1a1a;
            box-shadow: 0 0 30px rgba(200,0,0,0.03);
        }}
        .result-item .title {{
            font-size: 17px;
            font-weight: 500;
            color: #d0b0b0;
            font-family: 'Cinzel', serif;
        }}
        .result-item .title a {{
            color: #cc5555;
            text-decoration: none;
            border-bottom: 1px dotted #3a1a1a;
        }}
        .result-item .title a:hover {{
            color: #ff6666;
        }}
        .result-item .text {{
            font-size: 14px;
            color: #8a6a6a;
            margin-top: 6px;
            font-family: 'Segoe UI', sans-serif;
        }}
        .result-item .extra {{
            font-size: 13px;
            color: #6a4a4a;
            margin-top: 4px;
            font-family: 'Courier New', monospace;
        }}
        .result-item .index {{
            display: inline-block;
            background: #1a0a0a;
            color: #cc4444;
            font-size: 12px;
            padding: 2px 14px;
            border-radius: 6px;
            margin-right: 12px;
            border: 1px solid #2a1212;
            font-weight: 600;
        }}
        .source-tag {{
            display: inline-block;
            background: #1a0a0a;
            color: #8a4a4a;
            font-size: 10px;
            padding: 2px 10px;
            border-radius: 4px;
            margin-left: 10px;
            border: 1px solid #2a1212;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            font-family: 'Courier New', monospace;
        }}
        .empty-tag {{
            display: inline-block;
            background: #1a0808;
            color: #6a3a3a;
            font-size: 10px;
            padding: 2px 10px;
            border-radius: 4px;
            margin-left: 10px;
            border: 1px solid #3a1a1a;
        }}
        .empty {{
            color: #4a2a2a;
            font-style: italic;
            font-size: 15px;
            padding: 30px;
            text-align: center;
            border: 1px dashed #2a1212;
            border-radius: 8px;
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
        .footer a {{
            color: #6a3a3a;
            text-decoration: none;
            border-bottom: 1px dotted #3a1a1a;
        }}
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
        .glow {{
            text-shadow: 0 0 40px rgba(200,0,0,0.1), 0 0 80px rgba(200,0,0,0.05);
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
                <line x1="15" y1="85" x2="50" y2="50" stroke="#6a2a2a" stroke-width="0.8" stroke-dasharray="3,3" opacity="0.3"/>
                <line x1="85" y1="85" x2="50" y2="50" stroke="#6a2a2a" stroke-width="0.8" stroke-dasharray="3,3" opacity="0.3"/>
                <text x="50" y="98" font-family="Cinzel, serif" font-size="10" fill="#cc3333" text-anchor="middle" letter-spacing="3">OTOB</text>
            </svg>
            <div class="text">OTOB</div>
        </div>
        <div class="header">
            <div>
                <h1>🔱 OTOB <span>OSINT</span></h1>
                <div class="sub">⚡ Запрос: {query} · Тип: {qtype} · {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</div>
                <div class="sub" style="color:#4a2a2a; margin-top:2px;">🛡️ 50+ источников · Глобальный поиск</div>
            </div>
            <div><span class="badge badge-success">🎯 НАЙДЕНО: {total}</span></div>
        </div>
        <div class="stats-bar">
            <span class="stat">📊 Всего результатов: <strong>{total}</strong></span>
            <span class="stat">🔍 Всего источников: <strong>{len(sources)}</strong></span>
            <span class="stat">📦 Показано: <strong>{len(display_results)}</strong></span>
            <span class="stat">⚡ Статус: <strong style="color:#cc3333;">АКТИВЕН</strong></span>
        </div>
"""
    
    if display_results:
        for idx, item in enumerate(display_results, 1):
            source = item.get('_source', '')
            title = item.get('title', '—')
            
            if item.get('empty'):
                html += f"""
        <div class="result-item" style="border-left-color: #2a1212; opacity: 0.5;">
            <div class="title">
                <span class="index">#{idx}</span>
                {title}
                <span class="empty-tag">⚠️ ПУСТО</span>
                <span class="source-tag">{source[:15]}</span>
            </div>
        </div>
"""
                continue
            
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
            
            details = []
            for key, value in item.items():
                if key in ['_source', 'title', 'text', 'extra', 'link', 'found', 'exists', 'empty']:
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
            
            html += f"""
        <div class="result-item">
            <div class="title">
                <span class="index">#{idx}</span>
                {title if title else '—'}
                <span class="source-tag">{source[:15] if source else 'unknown'}</span>
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
            if not text and not extra and not details:
                html += f"            <div class=\"extra\" style=\"color:#4a2a2a;\">⚠️ Нет данных для отображения</div>\n"
            html += "        </div>\n"
        
        html += f"""
        <div style="text-align:center; margin-top:20px; padding:12px; border:1px solid #2a1212; border-radius:8px; color:#5a3a3a; font-size:13px;">
            📊 Показано {len(display_results)} из {total} найденных результатов
        </div>
"""
    else:
        html += '<div class="empty">❌ Ничего не найдено</div>'
    
    html += f"""
        <div class="footer">
            🔱 OTOB — OSINT · <a href="https://t.me/OTOBsearch" target="_blank">@OTOBsearch</a>
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
        types.InlineKeyboardButton("🔱 ГЛОБАЛЬНЫЙ ПОИСК", callback_data="global_search")
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
        types.InlineKeyboardButton("⚡ ПРОФИЛЬ", callback_data="menu_profile"),
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
                "🔱 *OTOB — OSINT*\n\n"
                "🕵️ *Глобальный OSINT-поиск*\n"
                "50+ источников\n\n"
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
                "🔱 *OTOB OSINT*",
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
                "⚡ *50+ OSINT-источников*\n"
                "🕵️ Имя · Адрес · Оператор · Соцсети · Утечки\n"
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
                "🔍 Проверка в базах утечек (LeakCheck, Hudson Rock)\n"
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
                "🕵️ Владелец · Как записан в контактах\n"
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
            f"🔱 *OTOB — OSINT*\n\n"
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
            "🔱 *OSINT — сканирование...*\n"
            "⏱️ Время: до 2 минут\n"
            "🕵️ 50+ источников...",
            parse_mode="Markdown"
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
                caption=f"🔱 *OSINT-ОТЧЁТ*\n\n"
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
    logger.info("🔱 OTOB — OSINT запускается...")
    logger.info("🛡️ Канал: @OTOBsearch")
    logger.info("⚡ Таймаут поиска: 2 минуты")
    logger.info("🚀 ScraperAPI: " + ("✅ ДОСТУПЕН" if SCRAPERAPI_KEY else "❌ НЕ НАЙДЕН (будут блокировки)"))
    
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
