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
from aiohttp_socks import SocksConnector

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

# ==================== АВТОЗАГРУЗКА ПРОКСИ ====================

class ProxyLoader:
    """Автоматически загружает прокси с публичных источников"""
    
    SOURCES = [
        # HTTP прокси (838 шт)
        "https://cdn.jsdelivr.net/gh/databay-labs/free-proxy-list/http.txt",
        # SOCKS5 прокси (294 шт)
        "https://cdn.jsdelivr.net/gh/databay-labs/free-proxy-list/socks5.txt",
        # Все прокси (693 шт)
        "https://raw.githubusercontent.com/Thordata/awesome-free-proxy-list/main/proxies/all.txt",
        # Резервный источник
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
    ]
    
    def __init__(self):
        self.proxies = []
        self.last_update = None
        self.update_interval = 600  # 10 минут
    
    async def fetch_proxies_from_url(self, url: str) -> list:
        """Загружает прокси из одного источника"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=15) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        proxies = []
                        for line in text.splitlines():
                            line = line.strip()
                            if line and not line.startswith('#'):
                                # Пробуем определить протокол
                                if '://' in line:
                                    proxies.append(line)
                                else:
                                    # Если нет протокола — добавляем socks5 по умолчанию
                                    proxies.append(f"socks5://{line}")
                        logger.info(f"✅ Загружено {len(proxies)} прокси из {url[:50]}...")
                        return proxies
        except Exception as e:
            logger.warning(f"⚠️ Ошибка загрузки {url[:50]}...: {e}")
        return []
    
    async def load_proxies(self):
        """Загружает прокси из всех источников"""
        all_proxies = []
        for url in self.SOURCES:
            proxies = await self.fetch_proxies_from_url(url)
            all_proxies.extend(proxies)
        
        # Очищаем и удаляем дубликаты
        unique_proxies = list(set(all_proxies))
        
        # Фильтруем только socks5 и http/https
        filtered = []
        for p in unique_proxies:
            if p.startswith(('socks5://', 'socks4://', 'http://', 'https://')):
                filtered.append(p)
        
        # Если ничего не загрузилось — используем запасные
        if not filtered:
            logger.warning("⚠️ Не удалось загрузить прокси. Использую запасные.")
            filtered = [
                "socks5://45.76.248.194:1080",
                "socks5://194.182.178.189:1080",
                "socks5://103.152.112.120:1080",
                "socks5://185.165.29.183:1080",
                "socks5://94.130.146.112:1080",
            ]
        
        self.proxies = filtered
        self.last_update = datetime.now()
        logger.info(f"✅ Всего загружено {len(self.proxies)} прокси")
        return self.proxies
    
    async def get_proxies(self, force_update: bool = False) -> list:
        """Возвращает список прокси (с обновлением если нужно)"""
        if force_update or not self.proxies or not self.last_update:
            await self.load_proxies()
        elif (datetime.now() - self.last_update).total_seconds() > self.update_interval:
            # Фоновое обновление
            asyncio.create_task(self.load_proxies())
        return self.proxies

# ==================== ИНИЦИАЛИЗАЦИЯ ПРОКСИ-МЕНЕДЖЕРА ====================

proxy_loader = ProxyLoader()

class ProxyManager:
    def __init__(self):
        self.proxies = []
        self.current_index = 0
        self.failed_proxies = set()
        self.lock = asyncio.Lock()
        self._initialized = False
    
    async def init(self):
        """Инициализация — загружаем прокси при старте"""
        if not self._initialized:
            self.proxies = await proxy_loader.get_proxies()
            self._initialized = True
            logger.info(f"🚀 Прокси-менеджер инициализирован: {len(self.proxies)} прокси")
    
    def get_next_proxy(self) -> str:
        """Возвращает следующий рабочий прокси"""
        if not self.proxies:
            return None
        
        # Если все прокси упали — сбрасываем и перезагружаем
        if len(self.failed_proxies) >= len(self.proxies):
            self.failed_proxies.clear()
            # Асинхронно обновляем список
            asyncio.create_task(proxy_loader.load_proxies())
        
        for _ in range(len(self.proxies)):
            proxy = self.proxies[self.current_index % len(self.proxies)]
            self.current_index += 1
            if proxy not in self.failed_proxies:
                return proxy
        
        self.failed_proxies.clear()
        return self.proxies[0] if self.proxies else None
    
    def mark_failed(self, proxy: str):
        """Помечает прокси как нерабочий"""
        if proxy in self.proxies:
            self.failed_proxies.add(proxy)
            logger.info(f"⚠️ Прокси {proxy[:30]}... помечен как нерабочий")
    
    async def request(self, url: str, method: str = "GET", headers: dict = None,
                      data: dict = None, timeout: int = 30, max_retries: int = 3) -> dict:
        """Выполняет запрос через прокси с автоматическим переключением"""
        headers = headers or {}
        headers.update({
            "User-Agent": random.choice([
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
            ]),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        })
        
        last_error = None
        
        for attempt in range(max_retries):
            proxy = self.get_next_proxy()
            
            try:
                if proxy:
                    if proxy.startswith(("socks5://", "socks4://")):
                        connector = SocksConnector.from_url(proxy)
                    else:
                        connector = aiohttp.TCPConnector()
                else:
                    connector = aiohttp.TCPConnector()
                
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.request(
                        method=method,
                        url=url,
                        headers=headers,
                        json=data if method == "POST" else None,
                        params=data if method == "GET" else None,
                        timeout=aiohttp.ClientTimeout(total=timeout)
                    ) as resp:
                        content_type = resp.headers.get("content-type", "")
                        if "application/json" in content_type:
                            result = await resp.json()
                        else:
                            result = {"text": await resp.text()}
                        
                        if resp.status == 200:
                            return {"success": True, "data": result, "status": resp.status, "proxy": proxy}
                        elif resp.status in [403, 429]:
                            if proxy:
                                self.mark_failed(proxy)
                            continue
                        else:
                            continue
                            
            except (aiohttp.ClientConnectorError, aiohttp.ClientProxyConnectionError) as e:
                if proxy:
                    self.mark_failed(proxy)
                last_error = e
            except asyncio.TimeoutError:
                if proxy:
                    self.mark_failed(proxy)
                last_error = "Timeout"
            except Exception as e:
                if proxy:
                    self.mark_failed(proxy)
                last_error = e
        
        return {"success": False, "error": str(last_error)}

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
proxy_manager = ProxyManager()

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
        f"OTOB — Osint Tool Olimpov Bot | {qtype.upper()} | {query}",
        f"OTOB | {query} | {qtype.upper()} | OSINT-отчёт",
        f"OSINT Tool Olimpov Bot — OTOB | {qtype} | {query}",
        f"OTOB — глобальный поиск | {query} | {qtype.upper()}",
    ]
    return random.choice(templates)

def safe_request(func):
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
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

# ==================== API ЧЕРЕЗ ПРОКСИ ====================

async def api_request_proxy(url: str, params: dict = None, timeout: int = 30) -> dict:
    result = await proxy_manager.request(url, method="GET", data=params, timeout=timeout)
    if result.get("success"):
        return result.get("data", {})
    return None

# ===== ВСЕ API ФУНКЦИИ (через прокси) =====

@safe_request
async def numverify_lookup(phone: str) -> dict:
    if not NUMVERIFY_KEY:
        return None
    clean = re.sub(r'\D', '', phone)
    url = f"https://api.numverify.com/validate?access_key={NUMVERIFY_KEY}&number={clean}"
    data = await api_request_proxy(url)
    if data and data.get('valid'):
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
    data = await api_request_proxy(url)
    if data and data.get('phone_valid'):
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
    data = await api_request_proxy(url)
    if data and data.get('valid'):
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
    data = await api_request_proxy(url)
    if data and data.get('valid'):
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
    data = await api_request_proxy(url)
    if data and data.get('is_valid_number'):
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
    data = await api_request_proxy(url)
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
    data = await api_request_proxy(url)
    if data and isinstance(data, dict) and data.get('text'):
        text = data.get('text', '')
        return {"status": "✅ Активен" if 'OK' in text else "❌ Не активен"}
    return None

@safe_request
async def hudsonrock_lookup(phone: str) -> dict:
    clean = re.sub(r'\D', '', phone)
    url = f"https://cavalier.hudsonrock.com/api/v1/search-by-username?username={clean}"
    data = await api_request_proxy(url)
    if data and data.get('total_results', 0) > 0:
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
    data = await api_request_proxy(url)
    if data:
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
    data = await api_request_proxy(url)
    if data:
        return {
            "reputation": data.get('reputation', '—'),
            "suspicious": data.get('suspicious', False),
            "references": data.get('references', 0)
        }
    return None

@safe_request
async def hibp_lookup(email: str) -> list:
    url = f"https://haveibeenpwned.com/api/v3/breachedaccount/{email}"
    data = await api_request_proxy(url)
    if data and isinstance(data, list):
        return [b.get('Name') for b in data]
    return []

@safe_request
async def ipinfo_lookup(ip: str) -> dict:
    url = f"https://ipinfo.io/{ip}/json"
    data = await api_request_proxy(url)
    if data:
        return {
            "country": data.get('country', '—'),
            "city": data.get('city', '—'),
            "region": data.get('region', '—'),
            "org": data.get('org', '—')
        }
    return None

@safe_request
async def ip_api_lookup(ip: str) -> dict:
    url = f"http://ip-api.com/json/{ip}"
    data = await api_request_proxy(url)
    if data and data.get('status') == 'success':
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
    data = await api_request_proxy(url)
    if data:
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
    url = f"https://t.me/{username}"
    result = await proxy_manager.request(url)
    if result.get("success"):
        return {"exists": True, "url": f"https://t.me/{username}"}
    return {"exists": False}

@safe_request
async def crtsh_lookup(domain: str) -> list:
    url = f"https://crt.sh/?q={domain}&output=json"
    data = await api_request_proxy(url)
    if data and isinstance(data, list):
        return [{"domain": item.get('name_value', '—')} for item in data[:5]]
    return []

@safe_request
async def whois_lookup(domain: str) -> dict:
    url = f"https://api.whois.vu/?q={domain}"
    data = await api_request_proxy(url)
    if data:
        return {
            "registrar": data.get('registrar', '—'),
            "creation_date": data.get('creation_date', '—'),
            "expiration_date": data.get('expiration_date', '—')
        }
    return None

@safe_request
async def numberlookup_api(phone: str) -> dict:
    clean = re.sub(r'\D', '', phone)
    url = f"https://numberlookupapi.com/api?number={clean}"
    data = await api_request_proxy(url)
    if data and data.get('valid'):
        return {
            "country": data.get('country', '—'),
            "carrier": data.get('carrier', '—'),
            "line_type": data.get('line_type', '—')
        }
    return None

@safe_request
async def zippopotam_lookup(postal_code: str, country: str = "RU") -> dict:
    url = f"https://api.zippopotam.us/{country}/{postal_code}"
    data = await api_request_proxy(url)
    if data:
        return {
            "country": data.get('country', '—'),
            "places": data.get('places', [])[:3]
        }
    return None

# ==================== ПАРСЕРЫ ЧЕРЕЗ ПРОКСИ ====================

async def parse_site_proxy(url: str, selectors: dict, max_results: int = 10) -> list:
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
        response = await proxy_manager.request(url, headers=headers, timeout=30)
        if response.get("success") and response.get("data"):
            html = response.get("data")
            if isinstance(html, dict) and html.get("text"):
                html = html["text"]
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
    return await parse_site_proxy(url, selectors, 15)

async def idcrawl_lookup(query: str) -> list:
    url = f"https://idcrawl.com/{query}"
    selectors = {"result": ".result-item, .profile-item", "title": ".title, .name", "link": "a", "text": ".description", "extra": ".extra"}
    return await parse_site_proxy(url, selectors, 15)

async def syncme_lookup(phone: str) -> list:
    url = f"https://sync.me/search?q={phone}"
    selectors = {"result": ".profile, .card, .result-item", "title": ".name, .title", "text": ".description", "extra": ".phone, .location"}
    return await parse_site_proxy(url, selectors, 5)

async def whoseno_lookup(phone: str) -> list:
    url = f"https://whoseno.com/search?q={phone}"
    selectors = {"result": ".result, .card", "title": ".name, .title", "text": ".description", "extra": ".phone"}
    return await parse_site_proxy(url, selectors, 5)

async def truepeoplesearch_lookup(query: str) -> list:
    if re.search(r'\d', query):
        url = f"https://truepeoplesearch.com/results?phoneno={query}"
    else:
        url = f"https://truepeoplesearch.com/results?name={query.replace(' ', '+')}"
    selectors = {"result": ".card, .person-item", "title": ".name, .title", "text": ".description", "extra": ".address, .phone, .relatives"}
    return await parse_site_proxy(url, selectors, 10)

async def truecaller_parse(phone: str) -> list:
    clean = re.sub(r'\D', '', phone)
    url = f"https://www.truecaller.com/search/{clean}"
    selectors = {"result": ".profile, .card, .result-item", "title": ".name, .title", "text": ".description", "extra": ".phone, .location"}
    return await parse_site_proxy(url, selectors, 5)

async def spokeo_parse(phone: str) -> list:
    clean = re.sub(r'\D', '', phone)
    url = f"https://www.spokeo.com/{clean}/search"
    selectors = {"result": ".result-item, .person-item", "title": ".name, .title", "text": ".description", "extra": ".address"}
    return await parse_site_proxy(url, selectors, 5)

async def whitepages_parse(phone: str) -> list:
    clean = re.sub(r'\D', '', phone)
    url = f"https://www.whitepages.com/phone/{clean}"
    selectors = {"result": ".card, .result-item", "title": ".name, .title", "text": ".
