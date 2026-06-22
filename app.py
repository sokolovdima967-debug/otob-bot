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

# ========== НАСТРОЙКИ (из переменных окружения) ==========
TOKEN = os.environ.get("TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "8545020464"))
DB_PATH = os.path.join("/tmp", "otob_bot.db")

# ===== КЛЮЧИ API (из переменных окружения) =====
VERIPHONE_KEY = os.environ.get("VERIPHONE_KEY")
OMKAR_KEY = os.environ.get("OMKAR_KEY")
NUMVERIFY_KEY = os.environ.get("NUMVERIFY_KEY")
ABSTRACT_API_KEY = os.environ.get("ABSTRACT_API_KEY")

if not TOKEN:
    raise ValueError("❌ TOKEN не установлен!")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== ХРАНИЛИЩЕ ОТЧЁТОВ ====================
reports = {}  # {report_id: {"query": str, "data": dict, "html": str, "created": timestamp}}

# ==================== ИНИЦИАЛИЗАЦИЯ БОТА ====================
bot = telebot.TeleBot(TOKEN, parse_mode="Markdown")
bot.remove_webhook()

# ==================== HTTP-СЕРВЕР ДЛЯ ОТОБРАЖЕНИЯ ОТЧЁТОВ ====================

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
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")

def run_http_server():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), ReportHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    logger.info(f"✅ HTTP-сервер запущен на порту {port}")

# Запускаем HTTP-сервер для отчётов
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

# ==================== ФУНКЦИИ ПОИСКА ====================

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
                            "type": data.get('phone_type', '—'),
                            "source": "veriphone.io"
                        }
    except Exception as e:
        logger.error(f"Veriphone error: {e}")
    return None

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
                            "country_code": data.get('country_code', '—'),
                            "source": "omkarcloud.com"
                        }
    except Exception as e:
        logger.error(f"OmkarCloud error: {e}")
    return None

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
                            "line_type": data.get('line_type', '—'),
                            "source": "numverify.com"
                        }
    except Exception as e:
        logger.error(f"Numverify error: {e}")
    return None

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
                            "line_type": data.get('line_type', '—'),
                            "source": "abstractapi.com"
                        }
    except Exception as e:
        logger.error(f"AbstractAPI error: {e}")
    return None

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
                            "timezone": data.get('timezone', '—'),
                            "source": "htmlweb.ru"
                        }
    except Exception as e:
        logger.error(f"HTMLWeb error: {e}")
    return None

async def hlr_lookup(phone: str) -> dict:
    try:
        clean = re.sub(r'\D', '', phone)
        url = f"https://smsc.ru/testhlr.php?phone={clean}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.text()
                    if 'OK' in data:
                        return {"status": "✅ Активен", "source": "smsc.ru"}
                    else:
                        return {"status": "❌ Не активен", "source": "smsc.ru"}
    except Exception as e:
        logger.error(f"HLR error: {e}")
    return None

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
                            "source": "HudsonRock",
                            "found": True,
                            "total": data.get('total_results', 0),
                            "breaches": data.get('results', [])[:5]
                        }
    except Exception as e:
        logger.error(f"HudsonRock error: {e}")
    return None

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
                        "references": data.get('references', 0),
                        "details": data.get('details', {}),
                        "source": "emailrep.io"
                    }
    except Exception as e:
        logger.error(f"EmailRep error: {e}")
    return None

async def hackmyip_breach_lookup(email: str) -> dict:
    try:
        url = f"https://hackmyip.com/api/breach?email={email}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('success'):
                        breach_data = data.get('data', {})
                        return {
                            "breaches": breach_data.get('breaches', 0),
                            "services": breach_data.get('services', []),
                            "risk_score": breach_data.get('risk', {}).get('score', 0),
                            "risk_level": breach_data.get('risk', {}).get('level', '—'),
                            "passwords": breach_data.get('passwords', {}),
                            "source": "hackmyip.com"
                        }
    except Exception as e:
        logger.error(f"HackMyIP Breach error: {e}")
    return None

async def rapid_email_verifier_lookup(email: str) -> dict:
    try:
        url = f"https://rapid-email-verifier.fly.dev/api/validate?email={email}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "valid": data.get('valid', False),
                        "domain": data.get('domain', '—'),
                        "disposable": data.get('disposable', False),
                        "mx": data.get('mx', False),
                        "source": "rapid-email-verifier"
                    }
    except Exception as e:
        logger.error(f"Rapid Email Verifier error: {e}")
    return None

async def bloombox_lookup(email: str) -> dict:
    try:
        url = f"https://bloombox.vercel.app/api/validate?email={email}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "valid": data.get('valid', False),
                        "disposable": data.get('disposable', False),
                        "free": data.get('free', False),
                        "role": data.get('role', False),
                        "mx": data.get('mx', False),
                        "smtp": data.get('smtp', False),
                        "source": "bloombox"
                    }
    except Exception as e:
        logger.error(f"Bloombox error: {e}")
    return None

async def tg_bot_retrieval_lookup(username: str) -> dict:
    try:
        clean = username.lstrip('@')
        url = f"https://tg-bot-retrieval-api.vercel.app/api/v1/bot/{clean}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "display_name": data.get('display_name', '—'),
                        "description": data.get('description', '—'),
                        "telegram_url": data.get('telegram_url', '—'),
                        "avatar_url": data.get('avatar_url', '—'),
                        "verified": data.get('verified', False),
                        "source": "tg-bot-retrieval"
                    }
    except Exception as e:
        logger.error(f"TGBotRetrieval error: {e}")
    return None

async def tginfo_lookup(username: str) -> dict:
    try:
        clean = username.lstrip('@')
        url = f"https://tginfo.vercel.app/api/info/{clean}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "id": data.get('id', '—'),
                        "username": data.get('username', '—'),
                        "first_name": data.get('first_name', '—'),
                        "last_name": data.get('last_name', '—'),
                        "bio": data.get('bio', '—'),
                        "type": data.get('type', '—'),
                        "source": "tginfo"
                    }
    except Exception as e:
        logger.error(f"Tginfo error: {e}")
    return None

async def duckduckgo_search(query: str) -> list:
    url = f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}"
    headers = {"User-Agent": "Mozilla/5.0"}
    results = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=15) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    for item in soup.select('.result')[:5]:
                        title_elem = item.select_one('.result__title a')
                        snippet_elem = item.select_one('.result__snippet')
                        if title_elem:
                            title = title_elem.get_text(strip=True)
                            link = title_elem.get('href')
                            if link and link.startswith('/'):
                                link = 'https://duckduckgo.com' + link
                            snippet = snippet_elem.get_text(strip=True) if snippet_elem else "Нет описания"
                            results.append({'title': title, 'snippet': snippet, 'link': link})
    except Exception as e:
        logger.error(f"DuckDuckGo error: {e}")
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
    
    if qtype == "phone":
        veriphone = await veriphone_lookup(query)
        if veriphone:
            result["sources"]["veriphone"] = veriphone
            total += 1
        
        omkar = await omkarcloud_lookup(query)
        if omkar:
            result["sources"]["omkarcloud"] = omkar
            total += 1
        
        numverify = await numverify_lookup(query)
        if numverify:
            result["sources"]["numverify"] = numverify
            total += 1
        
        abstract = await abstractapi_lookup(query)
        if abstract:
            result["sources"]["abstractapi"] = abstract
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
    
    if qtype == "email":
        emailrep = await emailrep_lookup(query)
        if emailrep:
            result["sources"]["emailrep"] = emailrep
            total += 1
        
        breach = await hackmyip_breach_lookup(query)
        if breach:
            result["sources"]["hackmyip_breach"] = breach
            total += 1
        
        validator = await rapid_email_verifier_lookup(query)
        if validator:
            result["sources"]["rapid_email_validator"] = validator
            total += 1
        
        bloombox = await bloombox_lookup(query)
        if bloombox:
            result["sources"]["bloombox"] = bloombox
            total += 1
    
    if qtype == "username" and query.startswith('@'):
        tg_bot = await tg_bot_retrieval_lookup(query)
        if tg_bot:
            result["sources"]["tg_bot_retrieval"] = tg_bot
            total += 1
        
        tginfo = await tginfo_lookup(query)
        if tginfo:
            result["sources"]["tginfo"] = tginfo
            total += 1
    
    web_results = await duckduckgo_search(query)
    if web_results:
        result["sources"]["web"] = web_results
        total += len(web_results)
    
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
                        all_results.append({"title": str(item), "source": source_name})
            elif isinstance(items, dict):
                all_results.append(items)
            else:
                all_results.append({"title": str(items), "source": source_name})
    
    base_url = os.environ.get("RENDER_EXTERNAL_URL", "https://otob-bot.onrender.com")
    
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
            background: #0d0d0d;
            color: #b0b0b0;
            font-family: 'Segoe UI', system-ui, sans-serif;
            padding: 30px 20px;
            line-height: 1.6;
            min-height: 100vh;
        }}
        .container {{
            max-width: 1000px;
            margin: 0 auto;
            background: #161616;
            border-radius: 10px;
            padding: 30px 35px;
            border: 1px solid #2a2a2a;
            box-shadow: 0 20px 60px rgba(0,0,0,0.9);
            position: relative;
        }}
        /* ===== ВОДЯНОЙ ЗНАК (глаз в лупе + OTOB) ===== */
        .watermark {{
            position: absolute;
            top: 20px;
            left: 25px;
            z-index: 10;
            opacity: 0.25;
            user-select: none;
            pointer-events: none;
            display: flex;
            flex-direction: column;
            align-items: center;
        }}
        .watermark svg {{
            width: 60px;
            height: 60px;
            filter: drop-shadow(0 0 10px rgba(0,0,0,0.5));
        }}
        .watermark .text {{
            color: #4a4a4a;
            font-size: 14px;
            font-weight: 700;
            letter-spacing: 3px;
            margin-top: 2px;
            text-transform: uppercase;
            font-family: 'Segoe UI', sans-serif;
        }}
        .header {{
            border-bottom: 1px solid #2a2a2a;
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
            color: #c8c8c8;
        }}
        .header h1 span {{
            color: #6a6a6a;
        }}
        .header .sub {{
            color: #6a6a6a;
            font-size: 13px;
            margin-top: 4px;
        }}
        .badge {{
            display: inline-block;
            background: #222222;
            padding: 3px 12px;
            border-radius: 4px;
            font-size: 12px;
            color: #8a8a8a;
            border: 1px solid #333333;
        }}
        .badge-success {{ background: #1a2a1a; color: #7aaa7a; border-color: #2a3a2a; }}
        .result-item {{
            margin: 12px 0;
            padding: 14px 18px;
            background: #121212;
            border-radius: 6px;
            border-left: 3px solid #2a2a2a;
        }}
        .result-item .title {{
            font-size: 16px;
            font-weight: 500;
            color: #c0c0c0;
        }}
        .result-item .title a {{
            color: #8a8a8a;
            text-decoration: none;
            border-bottom: 1px dotted #3a3a3a;
        }}
        .result-item .title a:hover {{
            color: #aaaaaa;
        }}
        .result-item .text {{
            font-size: 14px;
            color: #8a8a8a;
            margin-top: 6px;
        }}
        .result-item .extra {{
            font-size: 13px;
            color: #6a6a6a;
            margin-top: 4px;
        }}
        .result-item .index {{
            display: inline-block;
            background: #1a1a1a;
            color: #5a5a5a;
            font-size: 12px;
            padding: 1px 10px;
            border-radius: 4px;
            margin-right: 10px;
        }}
        .source-tag {{
            display: inline-block;
            background: #1a1a1a;
            color: #5a5a5a;
            font-size: 10px;
            padding: 1px 8px;
            border-radius: 3px;
            margin-left: 10px;
            border: 1px solid #262626;
        }}
        .empty {{ color: #555555; font-style: italic; font-size: 14px; padding: 20px; text-align: center; }}
        .stats {{ margin-top: 20px; padding: 12px 18px; background: #121212; border-radius: 6px; border: 1px solid #1a1a1a; color: #6a6a6a; font-size: 13px; text-align: center; }}
        .footer {{ margin-top: 25px; padding-top: 16px; border-top: 1px solid #1e1e1e; font-size: 12px; color: #4a4a4a; text-align: center; }}
        .footer a {{ color: #6a6a6a; text-decoration: none; }}
        @media (max-width: 600px) {{ .container {{ padding: 16px; }} .header {{ padding-left: 0; padding-top: 70px; }} .watermark {{ top: 10px; left: 15px; }} .watermark svg {{ width: 40px; height: 40px; }} .watermark .text {{ font-size: 10px; }} }}
    </style>
</head>
<body>
    <div class="container">
        <div class="watermark">
            <svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
                <circle cx="42" cy="42" r="28" stroke="#4a4a4a" stroke-width="4" fill="none"/>
                <line x1="62" y1="62" x2="88" y2="88" stroke="#4a4a4a" stroke-width="6" stroke-linecap="round"/>
                <ellipse cx="42" cy="42" rx="18" ry="14" stroke="#4a4a4a" stroke-width="2" fill="none"/>
                <circle cx="42" cy="42" r="6" stroke="#4a4a4a" stroke-width="2" fill="none"/>
                <circle cx="42" cy="42" r="2" fill="#4a4a4a"/>
                <circle cx="38" cy="38" r="3" fill="#4a4a4a" opacity="0.3"/>
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
            source = item.get('source', '')
            
            html += f"""
        <div class="result-item">
            <div class="title">
                <span class="index">#{idx}</span>
                {title}
                <span class="source-tag">{source}</span>
            </div>
"""
            if text and text != '—' and text != '':
                html += f"            <div class=\"text\">{text}</div>\n"
            if extra:
                html += f"            <div class=\"extra\">📎 {extra}</div>\n"
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

# ==================== МЕНЮ И КНОПКИ ====================

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

# ==================== ОБРАБОТЧИКИ КОМАНД ====================

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
            "ℹ️ Бот использует 15+ OSINT-источников.",
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

# ==================== КНОПКА ДЛЯ ОТКРЫТИЯ ОТЧЁТА ====================

@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("open_"))
def open_report_callback(call):
    bot.answer_callback_query(call.id)
    
    report_id = call.data.replace("open_", "")
    
    if report_id not in reports:
        bot.send_message(call.message.chat.id, "❌ Отчёт не найден. Повторите поиск.")
        return
    
    base_url = os.environ.get("RENDER_EXTERNAL_URL", "https://otob-bot.onrender.com")
    report_url = f"{base_url}/report/{report_id}"
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("📄 Открыть отчёт в браузере", url=report_url),
        types.InlineKeyboardButton("⬅️ Назад в меню", callback_data="menu_back")
    )
    
    bot.edit_message_text(
        f"✅ *Поиск завершён!*\n\n"
        f"🔍 Запрос: `{call.message.text}`\n"
        f"📊 Найдено: {reports[report_id]['data'].get('total_results', 0)} результатов\n\n"
        f"📄 Нажмите кнопку ниже, чтобы открыть полный отчёт в браузере.",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="Markdown",
        reply_markup=markup
    )

# ==================== ОБРАБОТЧИК ТЕКСТА ====================

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
        
        # Генерируем уникальный ID для отчёта
        report_id = f"{user_id}_{int(datetime.now().timestamp())}"
        
        # Генерируем HTML
        html = generate_html_report(text, data, report_id)
        
        # Сохраняем отчёт
        reports[report_id] = {
            "query": text,
            "data": data,
            "html": html,
            "created": datetime.now().timestamp()
        }
        
        # Удаляем старые отчёты (старше 1 часа)
        current_time = datetime.now().timestamp()
        for rid in list(reports.keys()):
            if current_time - reports[rid]["created"] > 3600:
                del reports[rid]
        
        # Отправляем сообщение с кнопкой
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("📄 Открыть отчёт", callback_data=f"open_{report_id}"),
            types.InlineKeyboardButton("⬅️ Назад в меню", callback_data="menu_back")
        )
        
        bot.edit_message_text(
            f"✅ *Поиск завершён!*\n\n"
            f"🔍 Запрос: `{text}`\n"
            f"📊 Найдено: **{total}** результатов\n"
            f"🔍 Осталось поисков: **{remaining}/3**\n\n"
            f"📄 Нажмите кнопку ниже, чтобы открыть полный отчёт.",
            chat_id,
            msg.message_id,
            parse_mode="Markdown",
            reply_markup=markup
        )
        
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
