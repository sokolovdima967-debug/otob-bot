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
from datetime import datetime
from bs4 import BeautifulSoup
import telebot
from telebot import types
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

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

if not TOKEN:
    raise ValueError("❌ TOKEN не установлен!")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== ХРАНИЛИЩЕ ОТЧЁТОВ ====================
reports = {}

# ==================== ИНИЦИАЛИЗАЦИЯ БОТА ====================
bot = telebot.TeleBot(TOKEN, parse_mode="Markdown")
bot.remove_webhook()

# ==================== HTTP-СЕРВЕР ====================

class ReportHandler(BaseHTTPRequestHandler):
    def do_GET(self):
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
    
    def do_HEAD(self):
        if self.path == '/' or self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

def run_http_server():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), ReportHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    logger.info(f"✅ HTTP-сервер запущен на порту {port}")

run_http_server()

# ==================== БАЗА ДАННЫХ ====================
def init_db():
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

def get_user(user_id: int, username: str = None):
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

def update_user(user_id: int, data: dict):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE users SET username = ?, searches_today = ?, searches_extra = ?, last_reset = ? WHERE user_id = ?",
                (data.get("username"), data.get("searches_today"), data.get("searches_extra"), data.get("last_reset"), user_id))
    conn.commit()
    conn.close()

def reset_daily_searches():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    today = datetime.now().date().isoformat()
    cur.execute("UPDATE users SET searches_today = 0, last_reset = ? WHERE last_reset != ?", (today, today))
    conn.commit()
    conn.close()

def can_search(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    reset_daily_searches()
    user = get_user(user_id)
    return user["searches_today"] < 3 or user["searches_extra"] > 0

def use_search(user_id: int) -> int:
    if user_id == ADMIN_ID:
        return 999
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

def get_remaining(user_id: int) -> int:
    if user_id == ADMIN_ID:
        return 999
    user = get_user(user_id)
    return (3 - user["searches_today"]) + user["searches_extra"]

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
    return "text"

# ==================== API ФУНКЦИИ ====================

# ----- 1. Numverify -----
async def numverify_lookup(phone: str) -> dict:
    if not NUMVERIFY_KEY:
        return None
    try:
        clean = re.sub(r'\D', '', phone)
        url = f"https://api.numverify.com/validate?access_key={NUMVERIFY_KEY}&number={clean}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('valid'):
                        return {
                            "country": data.get('country_name', '—'),
                            "location": data.get('location', '—'),
                            "carrier": data.get('carrier', '—'),
                            "line_type": data.get('line_type', '—')
                        }
    except Exception as e:
        logger.error(f"Numverify error: {e}")
    return None

# ----- 2. Veriphone -----
async def veriphone_lookup(phone: str) -> dict:
    if not VERIPHONE_KEY:
        return None
    try:
        clean = re.sub(r'\D', '', phone)
        url = f"https://api.veriphone.io/v2/verify?phone=%2B{clean}&key={VERIPHONE_KEY}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('phone_valid'):
                        return {
                            "country": data.get('country', '—'),
                            "carrier": data.get('carrier', '—'),
                            "type": data.get('phone_type', '—')
                        }
    except Exception as e:
        logger.error(f"Veriphone error: {e}")
    return None

# ----- 3. AbstractAPI -----
async def abstractapi_lookup(phone: str) -> dict:
    if not ABSTRACT_API_KEY:
        return None
    try:
        clean = re.sub(r'\D', '', phone)
        url = f"https://phonevalidation.abstractapi.com/v1/?api_key={ABSTRACT_API_KEY}&phone={clean}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('valid'):
                        return {
                            "country": data.get('country', {}).get('name', '—'),
                            "carrier": data.get('carrier', '—'),
                            "location": data.get('location', '—'),
                            "line_type": data.get('line_type', '—')
                        }
    except Exception as e:
        logger.error(f"AbstractAPI error: {e}")
    return None

# ----- 4. BigDataCloud -----
async def bigdatacloud_lookup(phone: str) -> dict:
    if not BIGDATACLOUD_KEY:
        return None
    try:
        clean = re.sub(r'\D', '', phone)
        url = f"https://api.bigdatacloud.net/data/phone-validate?phoneNumber=%2B{clean}&key={BIGDATACLOUD_KEY}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
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
    except Exception as e:
        logger.error(f"BigDataCloud error: {e}")
    return None

# ----- 5. OmkarCloud -----
async def omkarcloud_lookup(phone: str) -> dict:
    if not OMKAR_KEY:
        return None
    try:
        clean = re.sub(r'\D', '', phone)
        url = f"https://carrier-lookup-api.omkar.cloud/lookup?phone=%2B{clean}"
        headers = {"API-Key": OMKAR_KEY}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('is_valid_number'):
                        return {
                            "carrier": data.get('carrier', '—'),
                            "line_type": data.get('line_type', '—'),
                            "country_code": data.get('country_code', '—')
                        }
    except Exception as e:
        logger.error(f"OmkarCloud error: {e}")
    return None

# ----- 6. HTMLWeb.ru -----
async def htmlweb_lookup(phone: str) -> dict:
    try:
        clean = re.sub(r'\D', '', phone)
        url = f"https://htmlweb.ru/geo/api.php?json&telcod={clean}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data:
                        return {
                            "country": data.get('country', '—'),
                            "operator": data.get('operator', '—'),
                            "region": data.get('region', '—'),
                            "timezone": data.get('timezone', '—')
                        }
    except Exception as e:
        logger.error(f"HTMLWeb error: {e}")
    return None

# ----- 7. HLR (smsc.ru) -----
async def hlr_lookup(phone: str) -> dict:
    try:
        clean = re.sub(r'\D', '', phone)
        url = f"https://smsc.ru/testhlr.php?phone={clean}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.text()
                    if 'OK' in data:
                        return {"status": "✅ Активен"}
                    else:
                        return {"status": "❌ Не активен"}
    except Exception as e:
        logger.error(f"HLR error: {e}")
    return None

# ----- 8. Hudson Rock -----
async def hudsonrock_lookup(phone: str) -> dict:
    try:
        clean = re.sub(r'\D', '', phone)
        url = f"https://cavalier.hudsonrock.com/api/v1/search-by-username?username={clean}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('total_results', 0) > 0:
                        return {
                            "found": True,
                            "total": data.get('total_results', 0),
                            "breaches": data.get('results', [])[:5]
                        }
    except Exception as e:
        logger.error(f"HudsonRock error: {e}")
    return None

# ==================== ПАРСИНГ САЙТОВ ====================

async def parse_site(url: str, selectors: dict, max_results: int = 10) -> list:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    }
    results = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=15) as resp:
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

# ----- 9. X-Ray.contact -----
async def xray_lookup(query: str) -> list:
    url = f"https://x-ray.contact/search?q={query}"
    selectors = {"result": ".result-item, .social-link, .profile-item", "title": ".title, .name", "link": "a", "text": ".description", "extra": ".extra"}
    return await parse_site(url, selectors, 15)

# ----- 10. IDCrawl -----
async def idcrawl_lookup(query: str) -> list:
    url = f"https://idcrawl.com/{query}"
    selectors = {"result": ".result-item, .profile-item", "title": ".title, .name", "link": "a", "text": ".description", "extra": ".extra"}
    return await parse_site(url, selectors, 15)

# ----- 11. SpravkaRU -----
async def spravkaru_lookup(name: str) -> list:
    url = f"https://spravkaru.net/search?q={name.replace(' ', '+')}"
    selectors = {"result": ".person-item, .result-item", "title": ".name, .title", "text": ".description", "extra": ".phone, .address"}
    return await parse_site(url, selectors, 10)

# ----- 12. PeekYou -----
async def peekyou_lookup(name: str) -> list:
    url = f"https://peekyou.com/{name.replace(' ', '_')}"
    selectors = {"result": ".profile-item, .social-profile", "title": ".name, .title", "link": "a", "text": ".description", "extra": ".location"}
    return await parse_site(url, selectors, 10)

# ----- 13. TruePeopleSearch -----
async def truepeoplesearch_lookup(query: str) -> list:
    if re.search(r'\d', query):
        url = f"https://truepeoplesearch.com/results?phoneno={query}"
    else:
        url = f"https://truepeoplesearch.com/results?name={query.replace(' ', '+')}"
    selectors = {"result": ".card, .person-item", "title": ".name, .title", "text": ".description", "extra": ".address, .phone, .relatives"}
    return await parse_site(url, selectors, 10)

# ----- 14. Sync.me -----
async def syncme_lookup(phone: str) -> list:
    url = f"https://sync.me/search?q={phone}"
    selectors = {"result": ".profile, .card, .result-item", "title": ".name, .title", "text": ".description", "extra": ".phone, .location"}
    return await parse_site(url, selectors, 5)

# ----- 15. WhoSeNo -----
async def whoseno_lookup(phone: str) -> list:
    url = f"https://whoseno.com/search?q={phone}"
    selectors = {"result": ".result, .card", "title": ".name, .title", "text": ".description", "extra": ".phone"}
    return await parse_site(url, selectors, 5)

# ----- 16. DuckDuckGo -----
async def duckduckgo_search(query: str) -> list:
    url = f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}"
    selectors = {"result": ".result", "title": ".result__title a", "link": "a", "text": ".result__snippet"}
    return await parse_site(url, selectors, 5)

# ----- 17. Википедия -----
async def wikipedia_lookup(query: str) -> list:
    url = f"https://ru.wikipedia.org/wiki/{query.replace(' ', '_')}"
    selectors = {"result": ".mw-parser-output p", "title": "h1.firstHeading", "text": ".mw-parser-output p"}
    return await parse_site(url, selectors, 3)

# ==================== РФ РЕЕСТРЫ ====================

# ----- 18. ФССП -----
async def fssp_lookup(fio: str) -> dict:
    try:
        params = {"name": fio}
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api-ip.fssp.gov.ru/api/v1.0/search/physical", params=params, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("response", {}).get("count"):
                        return {
                            "found": True,
                            "count": data["response"]["count"],
                            "debts": data["response"].get("items", [])[:3]
                        }
    except Exception as e:
        logger.error(f"FSSP error: {e}")
    return {"found": False}

# ----- 19. ЕГРЮЛ (по ИНН) -----
async def egrul_lookup(inn: str) -> dict:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.atomno.ru/companies/{inn}?free=true", timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "name": data.get("fullName", "—"),
                        "ogrn": data.get("ogrn", "—"),
                        "status": data.get("status", "—"),
                        "director": data.get("director", {}).get("fullName", "—")
                    }
    except Exception as e:
        logger.error(f"EGRUL error: {e}")
    return None

# ----- 20. Минюст -----
async def minjust_lookup(query: str) -> list:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://minjust.gov.ru/uploaded/files/extremist_materials.csv", timeout=15) as resp:
                if resp.status == 200:
                    content = await resp.text()
                    reader = csv.DictReader(io.StringIO(content))
                    results = []
                    for row in reader:
                        if query.lower() in row.get("name", "").lower():
                            results.append(row)
                            if len(results) >= 3:
                                break
                    return results
    except Exception as e:
        logger.error(f"Minjust error: {e}")
    return []

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
    
    if qtype == "phone":
        # ===== API =====
        numverify = await numverify_lookup(query)
        if numverify:
            result["sources"]["numverify"] = numverify
            total += 1
        
        veriphone = await veriphone_lookup(query)
        if veriphone:
            result["sources"]["veriphone"] = veriphone
            total += 1
        
        abstract = await abstractapi_lookup(query)
        if abstract:
            result["sources"]["abstractapi"] = abstract
            total += 1
        
        bigdata = await bigdatacloud_lookup(query)
        if bigdata:
            result["sources"]["bigdatacloud"] = bigdata
            total += 1
        
        omkar = await omkarcloud_lookup(query)
        if omkar:
            result["sources"]["omkarcloud"] = omkar
            total += 1
        
        htmlweb = await htmlweb_lookup(query)
        if htmlweb:
            result["sources"]["htmlweb"] = htmlweb
            total += 1
        
        hlr = await hlr_lookup(query)
        if hlr:
            result["sources"]["hlr"] = hlr
            total += 1
        
        hudson = await hudsonrock_lookup(query)
        if hudson:
            result["sources"]["hudsonrock"] = hudson
            total += 1
        
        # ===== ПАРСИНГ =====
        xray = await xray_lookup(query)
        if xray:
            result["sources"]["xray"] = xray
            total += len(xray)
        
        idcrawl = await idcrawl_lookup(query)
        if idcrawl:
            result["sources"]["idcrawl"] = idcrawl
            total += len(idcrawl)
        
        syncme = await syncme_lookup(query)
        if syncme:
            result["sources"]["syncme"] = syncme
            total += len(syncme)
        
        whoseno = await whoseno_lookup(query)
        if whoseno:
            result["sources"]["whoseno"] = whoseno
            total += len(whoseno)
        
        truepeople = await truepeoplesearch_lookup(query)
        if truepeople:
            result["sources"]["truepeoplesearch"] = truepeople
            total += len(truepeople)
        
        # ===== РФ РЕЕСТРЫ =====
        fssp = await fssp_lookup(query)
        if fssp.get("found"):
            result["sources"]["fssp"] = fssp
            total += 1
        
        # ===== ОБЩИЕ =====
        ddg = await duckduckgo_search(query)
        if ddg:
            result["sources"]["duckduckgo"] = ddg
            total += len(ddg)
        
        wiki = await wikipedia_lookup(query)
        if wiki:
            result["sources"]["wikipedia"] = wiki
            total += len(wiki)
    
    # ===== ДЛЯ EMAIL =====
    if qtype == "email":
        emailrep = await emailrep_lookup(query)
        if emailrep:
            result["sources"]["emailrep"] = emailrep
            total += 1
        
        hibp = await hibp_lookup(query)
        if hibp:
            result["sources"]["hibp"] = hibp
            total += len(hibp)
    
    result["total_results"] = total
    return result

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

async def emailrep_lookup(email: str) -> dict:
    try:
        url = f"https://emailrep.io/{email}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "reputation": data.get('reputation', '—'),
                        "suspicious": data.get('suspicious', False),
                        "references": data.get('references', 0)
                    }
    except:
        pass
    return None

async def hibp_lookup(email: str) -> list:
    try:
        url = f"https://haveibeenpwned.com/api/v3/breachedaccount/{email}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return [b.get('Name') for b in data]
    except:
        pass
    return []

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
    
    html = f"""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OTOB — Osint Tool Olimpov Bot</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            background: #1a1a1a;
            color: #c8c8c8;
            font-family: 'Segoe UI', system-ui, sans-serif;
            padding: 30px 20px;
            line-height: 1.6;
            min-height: 100vh;
        }}
        .container {{
            max-width: 1000px;
            margin: 0 auto;
            background: #2a2a2a;
            border-radius: 10px;
            padding: 30px 35px;
            border: 1px solid #3a3a3a;
            box-shadow: 0 20px 60px rgba(0,0,0,0.7);
            position: relative;
        }}
        .watermark {{
            position: absolute;
            top: 20px;
            left: 25px;
            z-index: 10;
            opacity: 0.2;
            user-select: none;
            pointer-events: none;
            display: flex;
            flex-direction: column;
            align-items: center;
        }}
        .watermark svg {{
            width: 60px;
            height: 60px;
            filter: drop-shadow(0 0 10px rgba(0,0,0,0.3));
        }}
        .watermark .text {{
            color: #5a5a5a;
            font-size: 14px;
            font-weight: 700;
            letter-spacing: 3px;
            margin-top: 2px;
            text-transform: uppercase;
            font-family: 'Segoe UI', sans-serif;
        }}
        .header {{
            border-bottom: 1px solid #3a3a3a;
            padding-bottom: 18px;
            margin-bottom: 22px;
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            flex-wrap: wrap;
            padding-left: 80px;
        }}
        .header h1 {{
            font-size: 24px;
            font-weight: 600;
            color: #e0e0e0;
        }}
        .header h1 span {{
            color: #888888;
        }}
        .header .sub {{
            color: #888888;
            font-size: 13px;
            margin-top: 4px;
        }}
        .badge {{
            display: inline-block;
            background: #333333;
            padding: 3px 12px;
            border-radius: 4px;
            font-size: 12px;
            color: #aaaaaa;
            border: 1px solid #444444;
        }}
        .badge-success {{ background: #1a3a1a; color: #8acc8a; border-color: #2a5a2a; }}
        .result-item {{
            margin: 12px 0;
            padding: 14px 18px;
            background: #222222;
            border-radius: 6px;
            border-left: 3px solid #3a3a3a;
        }}
        .result-item .title {{
            font-size: 16px;
            font-weight: 500;
            color: #d0d0d0;
        }}
        .result-item .title a {{
            color: #9ab0d0;
            text-decoration: none;
            border-bottom: 1px dotted #4a5a6a;
        }}
        .result-item .text {{
            font-size: 14px;
            color: #aaaaaa;
            margin-top: 6px;
        }}
        .result-item .extra {{
            font-size: 13px;
            color: #888888;
            margin-top: 4px;
        }}
        .result-item .index {{
            display: inline-block;
            background: #2a2a2a;
            color: #777777;
            font-size: 12px;
            padding: 1px 10px;
            border-radius: 4px;
            margin-right: 10px;
        }}
        .empty {{ color: #666666; font-style: italic; font-size: 14px; padding: 20px; text-align: center; }}
        .stats {{ margin-top: 20px; padding: 12px 18px; background: #222222; border-radius: 6px; border: 1px solid #2a2a2a; color: #888888; font-size: 13px; text-align: center; }}
        .footer {{ margin-top: 25px; padding-top: 16px; border-top: 1px solid #2a2a2a; font-size: 12px; color: #666666; text-align: center; }}
        .footer a {{ color: #888888; text-decoration: none; }}
        .footer a:hover {{ color: #aaaaaa; }}
        @media (max-width: 600px) {{ .container {{ padding: 16px; }} .header {{ padding-left: 0; padding-top: 70px; }} .watermark {{ top: 10px; left: 15px; }} .watermark svg {{ width: 40px; height: 40px; }} .watermark .text {{ font-size: 10px; }} }}
    </style>
</head>
<body>
    <div class="container">
        <div class="watermark">
            <svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
                <circle cx="42" cy="42" r="28" stroke="#5a5a5a" stroke-width="4" fill="none"/>
                <line x1="62" y1="62" x2="88" y2="88" stroke="#5a5a5a" stroke-width="6" stroke-linecap="round"/>
                <ellipse cx="42" cy="42" rx="18" ry="14" stroke="#5a5a5a" stroke-width="2" fill="none"/>
                <circle cx="42" cy="42" r="6" stroke="#5a5a5a" stroke-width="2" fill="none"/>
                <circle cx="42" cy="42" r="2" fill="#5a5a5a"/>
                <circle cx="38" cy="38" r="3" fill="#5a5a5a" opacity="0.3"/>
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
        types.InlineKeyboardButton("🔍 Функции", callback_data="menu_functions"),
        types.InlineKeyboardButton("👤 Профиль", callback_data="menu_profile")
    )
    markup.add(
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
        types.InlineKeyboardButton("❓ Помощь", callback_data="menu_help"),
        types.InlineKeyboardButton("📊 Баланс", callback_data="menu_balance")
    )
    markup.add(
        types.InlineKeyboardButton("⬅️ Назад в меню", callback_data="menu_back")
    )
    return markup

# ==================== ОБРАБОТЧИКИ ====================

@bot.message_handler(commands=['start'])
def start_command(message):
    user_id = message.from_user.id
    remaining = get_remaining(user_id)
    
    bot.send_message(
        message.chat.id,
        f"🔍 *OTOB — Osint Tool Olimpov Bot*\n\n"
        f"📊 У тебя {remaining} поисков.\n\n"
        f"📌 *Выбери действие:*",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )

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
    except ValueError:
        bot.reply_to(message, "❌ Кол-во и ID должны быть числами.")

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
    except ValueError:
        bot.reply_to(message, "❌ Кол-во и ID должны быть числами.")

@bot.message_handler(commands=['users'])
def users_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Только для админа.")
        return
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

# ==================== ОБРАБОТЧИКИ КНОПОК ====================

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    bot.answer_callback_query(call.id)
    
    if call.data == "menu_back":
        bot.edit_message_text(
            f"🔍 *OTOB — Osint Tool Olimpov Bot*\n\n📌 *Выбери действие:*",
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
            "• 🌐 Глобальный поиск — номер, email, ФИО, IP, username\n\n"
            "📌 *Быстрый поиск:*\n"
            "• 📧 Email — проверка утечек\n"
            "• 📱 Телефон — оператор, регион\n"
            "• 👤 Username — поиск в соцсетях\n\n"
            "📌 *Дополнительно:*\n"
            "• ❓ Помощь\n"
            "• 📊 Баланс",
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
            f"👤 *Твой профиль*\n\n"
            f"🆔 ID: `{user.id}`\n"
            f"👤 Username: @{user.username or 'нет'}\n"
            f"📛 Имя: {user.first_name or '—'}\n"
            f"📊 Поисков сегодня: {user_data['searches_today']}/3\n"
            f"📊 Бонусных: {user_data['searches_extra']}\n"
            f"📊 Всего доступно: {remaining}\n"
            f"⏰ Сброс: в 00:00 МСК\n"
            f"👑 Админ: {'✅' if user.id == ADMIN_ID else '❌'}"
        )
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("⬅️ Назад в меню", callback_data="menu_back")
            )
        )
        return
    
    if call.data == "menu_help":
        bot.edit_message_text(
            "❓ *Помощь*\n\n"
            "📌 *Как пользоваться:*\n"
            "• Отправь номер, email, никнейм или IP\n"
            "• Глобальный поиск — всё в одном запросе\n\n"
            "📊 *Лимит:* 3 поиска в день (сброс в 00:00 МСК)\n"
            "👑 *Админ:* безлимитный доступ\n\n"
            "🧑‍💻 *Канал разработчиков:* @lkblyad",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("⬅️ Назад в меню", callback_data="menu_back")
            )
        )
        return
    
    if call.data == "menu_balance":
        user_id = call.from_user.id
        remaining = get_remaining(user_id)
        used = get_user(user_id)["searches_today"]
        extra = get_user(user_id)["searches_extra"]
        text = (
            f"📊 *Твой баланс*\n\n"
            f"🔍 Использовано сегодня: {used}/3\n"
            f"📊 Бонусных запросов: {extra}\n"
            f"📊 Всего доступно: {remaining}\n"
            f"⏰ Сброс: в 00:00 МСК\n\n"
            f"👑 Админ: {'безлимитный' if user_id == ADMIN_ID else 'нет'}"
        )
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("⬅️ Назад в меню", callback_data="menu_back")
            )
        )
        return
    
    if call.data == "global_search":
        bot.edit_message_text(
            "🌐 *Глобальный поиск*\n\n"
            "Отправь запрос для поиска:\n"
            "• Номер телефона: +79991234567\n"
            "• ФИО: Иванов Иван Иванович\n"
            "• Email: user@example.com\n"
            "• Никнейм: username\n"
            "• IP-адрес: 8.8.8.8\n"
            "• Любой текст\n\n"
            "ℹ️ Бот использует 22+ OSINT-источника.",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("⬅️ Назад в меню", callback_data="menu_back")
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
                types.InlineKeyboardButton("⬅️ Назад в меню", callback_data="menu_back")
            )
        )
        return
    
    if call.data == "phone_search":
        bot.edit_message_text(
            "📱 *Проверка телефона*\n\n"
            "Отправь номер для проверки.\n\n"
            "Пример: +79991234567 или 79991234567",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("⬅️ Назад в меню", callback_data="menu_back")
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
                types.InlineKeyboardButton("⬅️ Назад в меню", callback_data="menu_back")
            )
        )
        return

# ==================== ОСНОВНОЙ ОБРАБОТЧИК ====================

@bot.message_handler(func=lambda message: True)
def handle_text(message):
    text = message.text.strip()
    if not text or text.startswith('/'):
        return
    
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    if not can_search(user_id):
        bot.reply_to(message, "❌ *Лимит поисков исчерпан!*", parse_mode="Markdown")
        return
    
    msg = bot.reply_to(message, "⏳ Выполняется глобальный поиск...")
    
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        data = loop.run_until_complete(global_lookup(text))
        loop.close()
        
        total = data.get("total_results", 0)
        remaining = use_search(user_id)
        
        report_id = f"{user_id}_{int(datetime.now().timestamp())}"
        html = generate_html_report(text, data, report_id)
        
        reports[report_id] = {
            "query": text,
            "data": data,
            "html": html,
            "created": datetime.now().timestamp()
        }
        
        filename = f"{user_id}_{int(datetime.now().timestamp())}.html"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(html)
        
        with open(filename, "rb") as f:
            bot.send_document(
                chat_id,
                f,
                caption=f"📊 *OSINT-отчёт*\n\n"
                        f"🔍 Запрос: `{text}`\n"
                        f"📌 Найдено результатов: **{total}**\n"
                        f"🔍 Осталось поисков: **{remaining}/3**",
                parse_mode="Markdown"
            )
        
        os.remove(filename)
        bot.delete_message(chat_id, msg.message_id)
        
    except Exception as e:
        bot.edit_message_text(
            f"⚠️ Ошибка: {str(e)[:100]}",
            chat_id,
            msg.message_id
        )

# ==================== ЗАПУСК ====================

if __name__ == "__main__":
    init_db()
    logger.info("🚀 OTOB бот запускается...")
    bot.remove_webhook()
    bot.infinity_polling(timeout=60, long_polling_timeout=30)
