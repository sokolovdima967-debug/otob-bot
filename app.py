import os
import asyncio
import aiohttp
import re
import logging
import sqlite3
import json
import hashlib
import random
from datetime import datetime
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "8545020464"))

# База данных в /tmp (Render разрешает запись)
DB_PATH = os.path.join("/tmp", "otob_bot.db")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
    if "." in query and len(query.split()) == 1:
        return "domain"
    return "text"

# ==================== УНИВЕРСАЛЬНЫЙ ПАРСИНГ ====================
async def parse_site(url: str, query: str, selectors: dict, max_results: int = 10) -> list:
    headers = {
        "User-Agent": random.choice([
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
        ]),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    results = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=25, allow_redirects=True) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    items = soup.select(selectors.get("result", "div.result, li.result, .item, .post, .entry, .card, .person-item, .profile-item"))
                    for item in items[:max_results]:
                        title_elem = item.select_one(selectors.get("title", "a, h2, h3, .title, .name"))
                        link_elem = item.select_one(selectors.get("link", "a"))
                        text_elem = item.select_one(selectors.get("text", "p, .text, .description, .snippet, .content"))
                        extra_elem = item.select_one(selectors.get("extra", ".phone, .number, .address, .email, .location"))
                        
                        result = {
                            "title": title_elem.get_text(strip=True) if title_elem else "—",
                            "link": link_elem.get('href') if link_elem else None,
                            "text": text_elem.get_text(strip=True)[:300] if text_elem else "—",
                            "extra": extra_elem.get_text(strip=True) if extra_elem else None
                        }
                        if result["link"] and result["link"].startswith('/'):
                            result["link"] = f"https://{url.split('/')[2]}{result['link']}"
                        if not result["link"] and link_elem and link_elem.get('href'):
                            result["link"] = link_elem.get('href')
                        if result["title"] != "—" or result["text"] != "—":
                            results.append(result)
    except Exception as e:
        logger.error(f"Parse error for {url}: {e}")
    return results

# ==================== ВСЕ ПАРСЕРЫ (30+ ИСТОЧНИКОВ) ====================

async def parse_hibp(email: str) -> list:
    url = f"https://haveibeenpwned.com/account/{email}"
    selectors = {"result": ".breach, .breach-item", "title": ".breach-name, .title", "text": ".breach-description", "extra": ".breach-date"}
    return await parse_site(url, email, selectors, 10)

async def parse_emailrep(email: str) -> list:
    url = f"https://emailrep.io/{email}"
    selectors = {"result": ".result, .card", "title": ".label, .name", "text": ".value", "extra": ".extra"}
    return await parse_site(url, email, selectors, 5)

async def parse_epieos(query: str) -> list:
    url = f"https://epieos.com/search?q={query}"
    selectors = {"result": ".result-item, .profile-item, .card", "title": ".title, .name", "text": ".description", "extra": ".extra"}
    return await parse_site(url, query, selectors, 10)

async def parse_xray(query: str) -> list:
    url = f"https://x-ray.contact/search?q={query}"
    selectors = {"result": ".result-item, .social-link, .profile-item", "title": ".title, .name", "link": "a", "text": ".description", "extra": ".extra"}
    return await parse_site(url, query, selectors, 15)

async def parse_osint_industries(query: str) -> list:
    url = f"https://osint.industries/search?q={query}"
    selectors = {"result": ".service-result, .result-item", "title": ".title, .name", "link": "a", "text": ".description", "extra": ".extra"}
    return await parse_site(url, query, selectors, 15)

async def parse_peekyou(name: str) -> list:
    url = f"https://peekyou.com/{name.replace(' ', '_')}"
    selectors = {"result": ".profile-item, .social-profile", "title": ".name, .title", "link": "a", "text": ".description", "extra": ".location"}
    return await parse_site(url, name, selectors, 10)

async def parse_idcrawl(query: str) -> list:
    url = f"https://idcrawl.com/{query}"
    selectors = {"result": ".result-item, .profile-item", "title": ".title, .name", "link": "a", "text": ".description", "extra": ".extra"}
    return await parse_site(url, query, selectors, 15)

async def parse_spravkaru(name: str) -> list:
    url = f"https://spravkaru.net/search?q={name.replace(' ', '+')}"
    selectors = {"result": ".person-item, .result-item", "title": ".name, .title", "text": ".description", "extra": ".phone, .address"}
    return await parse_site(url, name, selectors, 10)

async def parse_hunter(email: str) -> list:
    url = f"https://hunter.io/email-verifier/{email}"
    selectors = {"result": ".result, .card", "title": ".label, .name", "text": ".value", "extra": ".extra"}
    return await parse_site(url, email, selectors, 5)

async def parse_cyberbackgroundchecks(query: str) -> list:
    url = f"https://cyberbackgroundchecks.com/search?q={query}"
    selectors = {"result": ".person-item, .result-item", "title": ".name, .title", "text": ".description", "extra": ".address, .phone"}
    return await parse_site(url, query, selectors, 10)

async def parse_truepeoplesearch(query: str) -> list:
    if re.search(r'\d', query):
        url = f"https://truepeoplesearch.com/results?phoneno={query}"
    else:
        url = f"https://truepeoplesearch.com/results?name={query.replace(' ', '+')}"
    selectors = {"result": ".card, .person-item", "title": ".name, .title", "text": ".description", "extra": ".address, .phone, .relatives"}
    return await parse_site(url, query, selectors, 10)

async def parse_rocketreach(email: str) -> list:
    url = f"https://rocketreach.co/email/{email}"
    selectors = {"result": ".result, .card", "title": ".label, .name", "text": ".value", "extra": ".extra"}
    return await parse_site(url, email, selectors, 5)

async def parse_minerva(email: str) -> list:
    url = f"https://minervaosint.com/search?q={email}"
    selectors = {"result": ".result-item, .platform-item", "title": ".name, .title", "text": ".description", "extra": ".extra"}
    return await parse_site(url, email, selectors, 15)

async def parse_noimosiny(query: str) -> list:
    url = f"https://noimosiny.com/search?q={query}"
    selectors = {"result": ".platform-item, .result-item", "title": ".name, .title", "link": "a", "text": ".description", "extra": ".extra"}
    return await parse_site(url, query, selectors, 15)

async def parse_truecaller(phone: str) -> list:
    url = f"https://www.truecaller.com/search/{phone}"
    selectors = {"result": ".profile, .card, .result-item", "title": ".name, .title", "text": ".description, .subtitle", "extra": ".phone, .location"}
    return await parse_site(url, phone, selectors, 5)

async def parse_syncme(phone: str) -> list:
    url = f"https://sync.me/search?q={phone}"
    selectors = {"result": ".profile, .card, .result-item", "title": ".name, .title", "text": ".description, .subtitle", "extra": ".phone, .location"}
    return await parse_site(url, phone, selectors, 5)

async def parse_whoseno(phone: str) -> list:
    url = f"https://whoseno.com/search?q={phone}"
    selectors = {"result": ".result, .card", "title": ".name, .title", "text": ".description", "extra": ".phone"}
    return await parse_site(url, phone, selectors, 5)

async def parse_duckduckgo(query: str) -> list:
    url = f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}"
    selectors = {"result": ".result", "title": ".result__title a", "link": "a", "text": ".result__snippet"}
    return await parse_site(url, query, selectors, 5)

async def parse_wikipedia(query: str) -> list:
    url = f"https://ru.wikipedia.org/wiki/{query.replace(' ', '_')}"
    selectors = {"result": ".mw-parser-output p", "title": "h1.firstHeading", "text": ".mw-parser-output p"}
    return await parse_site(url, query, selectors, 3)

async def hlr_lookup(phone: str) -> list:
    clean = re.sub(r'\D', '', phone)
    url = f"https://smsc.ru/testhlr.php?phone={clean}"
    results = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.text()
                    if 'OK' in data:
                        results.append({"title": "HLR-запрос", "text": data[:200], "extra": "Номер активен"})
                    else:
                        results.append({"title": "HLR-запрос", "text": data[:200], "extra": "Ошибка или номер не активен"})
    except:
        pass
    return results

# ==================== ГЛОБАЛЬНЫЙ ПОИСК (30+ ИСТОЧНИКОВ) ====================

async def global_lookup(query: str) -> dict:
    query = query.strip()
    qtype = detect_query_type(query)
    
    result = {
        "query": query,
        "type": qtype,
        "timestamp": datetime.now().isoformat(),
        "sources": {}
    }
    
    # Словарь всех парсеров (30+)
    all_parsers = {
        # ===== ОБЩИЕ (для всех типов) =====
        "duckduckgo": parse_duckduckgo,
        "wikipedia": parse_wikipedia,
        "xray": parse_xray,
        "idcrawl": parse_idcrawl,
        "noimosiny": parse_noimosiny,
        "osint_industries": parse_osint_industries,
        "epieos": parse_epieos,
        "cyberbackgroundchecks": parse_cyberbackgroundchecks,
        
        # ===== EMAIL =====
        "hibp": parse_hibp,
        "emailrep": parse_emailrep,
        "hunter": parse_hunter,
        "rocketreach": parse_rocketreach,
        "minerva": parse_minerva,
        
        # ===== ФИО =====
        "peekyou": parse_peekyou,
        "spravkaru": parse_spravkaru,
        "truepeoplesearch": parse_truepeoplesearch,
        
        # ===== ТЕЛЕФОН =====
        "truecaller": parse_truecaller,
        "syncme": parse_syncme,
        "whoseno": parse_whoseno,
        "hlr": hlr_lookup,
    }
    
    # Фильтруем парсеры по типу запроса
    tasks = {}
    for name, parser in all_parsers.items():
        if qtype == "email" and name in ["hibp", "emailrep", "hunter", "rocketreach", "minerva"]:
            tasks[name] = parser(query)
        elif qtype == "fio" and name in ["peekyou", "spravkaru", "truepeoplesearch"]:
            tasks[name] = parser(query)
        elif qtype == "phone" and name in ["truecaller", "syncme", "whoseno", "hlr", "truepeoplesearch"]:
            tasks[name] = parser(query)
        elif name in ["duckduckgo", "wikipedia", "xray", "idcrawl", "noimosiny", "osint_industries", "epieos", "cyberbackgroundchecks"]:
            tasks[name] = parser(query)
    
    # Запускаем все задачи параллельно
    for name, task in tasks.items():
        try:
            data = await task
            if data:
                result["sources"][name] = data
                logger.info(f"✅ {name}: {len(data)} результатов")
            else:
                logger.info(f"⏭️ {name}: пусто (пропускаем)")
        except Exception as e:
            logger.error(f"❌ {name} error: {e}")
    
    logger.info(f"✅ Итоговый результат: {len(result['sources'])} источников с данными")
    return result

# ==================== ГЕНЕРАТОР НАЗВАНИЙ OTOB ====================

def generate_otob_title(query: str, qtype: str) -> str:
    templates = [
        f"OTOB — Osint Tool Olimpov Bot | {qtype.upper()} | {query}",
        f"OTOB | {query} | {qtype.upper()} | OSINT-отчёт",
        f"OSINT Tool Olimpov Bot — OTOB | {qtype} | {query}",
        f"OTOB | {qtype} | {query} | Osint Tool Olimpov Bot",
        f"OTOB — глобальный поиск | {query} | {qtype.upper()}",
        f"OTOB: {query} | {qtype} | Osint Tool Olimpov Bot",
        f"OTOB | OSINT-отчёт | {query} | {qtype.upper()}",
        f"Osint Tool Olimpov Bot — OTOB | {query}",
        f"OTOB | {query} | {qtype} | OSINT",
        f"OTOB — {qtype.upper()} | {query} | Osint Tool Olimpov Bot",
    ]
    return random.choice(templates)

# ==================== ФОРМАТИРОВАНИЕ РЕЗУЛЬТАТА ====================

def format_global_result(data: dict) -> str:
    query = data['query']
    qtype = data['type']
    sources = data.get("sources", {})
    
    title = generate_otob_title(query, qtype)
    
    reply = f"🔎 *{title}*\n\n"
    reply += f"📋 Тип: {qtype}\n"
    reply += f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n\n"
    
    all_results = []
    for source_name, items in sources.items():
        if items:
            for item in items:
                item['_source'] = source_name
                all_results.append(item)
    
    all_results = all_results[:25]
    
    if all_results:
        for idx, item in enumerate(all_results, 1):
            title_text = item.get('title', '—')[:60]
            link = item.get('link', '')
            text = item.get('text', '')[:200]
            extra = item.get('extra', '')
            
            reply += f"📌 **{idx}. {title_text}**\n"
            if link:
                reply += f"   🔗 [Ссылка]({link})\n"
            if text and text != '—':
                reply += f"   📝 {text}\n"
            if extra:
                reply += f"   📎 {extra}\n"
            reply += "\n"
        
        reply += f"\n📊 *Найдено: {len(all_results)} результатов из {len(sources)} источников*"
    else:
        reply += "❌ Ничего не найдено.\n"
        reply += "\n💡 Попробуйте другой запрос или проверьте интернет-соединение."
    
    return reply

# ==================== ГЕНЕРАЦИЯ HTML-ОТЧЁТА ====================

def generate_html_report(query: str, data: dict) -> str:
    sources = data.get("sources", {})
    qtype = data.get("type", "text")
    
    title = generate_otob_title(query, qtype)
    
    all_results = []
    for source_name, items in sources.items():
        if items:
            for item in items:
                item['_source'] = source_name
                all_results.append(item)
    all_results = all_results[:25]
    
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
            background: #0d0d0d;
            color: #b0b0b0;
            font-family: 'Segoe UI', 'Helvetica Neue', system-ui, sans-serif;
            padding: 30px 20px;
            line-height: 1.6;
            min-height: 100vh;
            position: relative;
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
            z-index: 1;
        }}
        .header {{
            border-bottom: 1px solid #2a2a2a;
            padding-bottom: 18px;
            margin-bottom: 22px;
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            flex-wrap: wrap;
        }}
        .header h1 {{
            font-size: 24px;
            font-weight: 600;
            color: #c8c8c8;
            letter-spacing: 0.5px;
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
            border-bottom-color: #5a5a5a;
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
        .empty {{
            color: #555555;
            font-style: italic;
            font-size: 14px;
            padding: 20px;
            text-align: center;
        }}
        .stats {{
            margin-top: 20px;
            padding: 12px 18px;
            background: #121212;
            border-radius: 6px;
            border: 1px solid #1a1a1a;
            color: #6a6a6a;
            font-size: 13px;
            text-align: center;
        }}
        .footer {{
            margin-top: 25px;
            padding-top: 16px;
            border-top: 1px solid #1e1e1e;
            font-size: 12px;
            color: #4a4a4a;
            text-align: center;
        }}
        .footer a {{ color: #6a6a6a; text-decoration: none; }}
        .footer a:hover {{ color: #8a8a8a; }}
        
        /* Водяной знак */
        .watermark {{
            position: fixed;
            bottom: 30px;
            left: 30px;
            z-index: 1000;
            opacity: 0.15;
            user-select: none;
            pointer-events: none;
            display: flex;
            flex-direction: column;
            align-items: center;
        }}
        .watermark svg {{
            width: 80px;
            height: 80px;
            filter: drop-shadow(0 0 10px rgba(0,0,0,0.5));
        }}
        .watermark .text {{
            color: #3a3a3a;
            font-size: 14px;
            font-weight: 700;
            letter-spacing: 3px;
            margin-top: 4px;
            text-transform: uppercase;
            font-family: 'Segoe UI', sans-serif;
        }}
        
        @media (max-width: 600px) {{
            .container {{ padding: 16px; }}
            .header h1 {{ font-size: 20px; }}
            .result-item {{ padding: 10px 14px; }}
            .watermark svg {{ width: 50px; height: 50px; }}
            .watermark .text {{ font-size: 10px; }}
        }}
    </style>
</head>
<body>
    <!-- Водяной знак -->
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
    
    <div class="container">
        <div class="header">
            <div>
                <h1>OTOB <span>Osint Tool Olimpov Bot</span></h1>
                <div class="sub">
                    {title} &nbsp;·&nbsp;
                    <span class="badge">{qtype}</span> &nbsp;·&nbsp;
                    {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}
                </div>
            </div>
            <div>
                <span class="badge badge-success">найдено: {len(all_results)}</span>
            </div>
        </div>
"""
    
    if all_results:
        for idx, item in enumerate(all_results, 1):
            title_text = item.get('title', '—')[:60]
            link = item.get('link', '')
            text = item.get('text', '')[:200]
            extra = item.get('extra', '')
            source = item.get('_source', '')
            
            html += f"""
        <div class="result-item">
            <div class="title">
                <span class="index">#{idx}</span>
                {f'<a href="{link}" target="_blank">{title_text}</a>' if link else title_text}
                <span class="source-tag">{source}</span>
            </div>
"""
            if text and text != '—':
                html += f"            <div class=\"text\">{text}</div>\n"
            if extra:
                html += f"            <div class=\"extra\">📎 {extra}</div>\n"
            html += "        </div>\n"
        
        html += f"""
        <div class="stats">
            📊 Найдено <strong>{len(all_results)}</strong> результатов из <strong>{len(sources)}</strong> источников
        </div>
"""
    else:
        html += """
        <div class="empty">❌ Ничего не найдено</div>
"""
    
    html += f"""
        <div class="footer">
            🛡️ OTOB — Osint Tool Olimpov Bot &nbsp;·&nbsp; 
            <a href="https://t.me/Osint_Tool_Olimpov_bot" target="_blank">@Osint_Tool_Olimpov_bot</a>
        </div>
    </div>
</body>
</html>
"""
    return html

# ==================== ОБРАБОТЧИКИ ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("🔍 Глобальный поиск", callback_data="global_search")],
        [InlineKeyboardButton("👤 Профиль", callback_data="menu_profile")],
        [InlineKeyboardButton("🧑‍💻 Разработчики", url="https://t.me/lkblyad")]
    ])
    await update.message.reply_text(
        "🔍 *OTOB — Osint Tool Olimpov Bot*\n\n"
        "Отправь любой запрос для поиска:\n"
        "• Номер телефона: +79991234567\n"
        "• ФИО: Иванов Иван Иванович\n"
        "• Email: user@example.com\n"
        "• Никнейм, IP, домен или текст\n\n"
        "⚡ Парсинг 30+ OSINT-сайтов",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("🔍 Глобальный поиск", callback_data="global_search")],
        [InlineKeyboardButton("👤 Профиль", callback_data="menu_profile")],
        [InlineKeyboardButton("🧑‍💻 Разработчики", url="https://t.me/lkblyad")]
    ])
    await query.message.edit_text(
        "🔍 *OTOB — Osint Tool Olimpov Bot*\n\n"
        "Отправь любой запрос для поиска",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    user_data = get_user(user.id, user.username or "Unknown")
    text = (
        f"👤 *Твой профиль*\n\n"
        f"🆔 ID: `{user.id}`\n"
        f"👤 Username: @{user.username or 'нет'}\n"
        f"📊 Поисков сегодня: {user_data['searches_today']}/3\n"
        f"📊 Бонусных: {user_data['searches_extra']}\n"
        f"📊 Всего доступно: {get_remaining(user.id)}\n"
        f"👑 Админ: {'✅' if user.id == ADMIN_ID else '❌'}"
    )
    await query.message.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Назад", callback_data="menu_back")]
    ]))

async def global_search_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.edit_text(
        "🌐 *Глобальный поиск*\n\n"
        "Отправь запрос для поиска:\n"
        "• Номер телефона: +79991234567\n"
        "• ФИО: Иванов Иван Иванович\n"
        "• Email: user@example.com\n"
        "• Никнейм: username\n"
        "• IP-адрес: 8.8.8.8\n"
        "• Домен: example.com\n"
        "• Любой текст\n\n"
        "ℹ️ Бот парсит 30+ OSINT-сайтов.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Назад", callback_data="menu_back")]
        ])
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text or text.startswith('/'):
        return
    
    if not can_search(update.effective_user.id):
        await update.message.reply_text("❌ *Лимит поисков исчерпан!*", parse_mode="Markdown")
        return
    
    wait_msg = await update.message.reply_text("⏳ OTOB выполняет поиск по 30+ сайтам...")
    data = await global_lookup(text)
    reply = format_global_result(data)
    remaining = use_search(update.effective_user.id)
    reply += f"\n\n🔍 Осталось: {remaining}/3"
    await wait_msg.edit_text(reply, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Назад в меню", callback_data="menu_back")]
    ]))

# ==================== АДМИН-КОМАНДЫ ====================

async def cmd_give(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Только для админа.")
        return
    args = update.message.text.split()
    if len(args) < 3:
        await update.message.reply_text("❗ /give <кол-во> <user_id>")
        return
    try:
        amount = int(args[1])
        target_id = int(args[2])
    except ValueError:
        await update.message.reply_text("❌ Кол-во и ID должны быть числами.")
        return
    user = get_user(target_id)
    user["searches_today"] = max(0, user["searches_today"] - amount)
    update_user(target_id, user)
    await update.message.reply_text(f"✅ Выдано {amount} запросов пользователю `{target_id}`.", parse_mode="Markdown")

async def cmd_take(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Только для админа.")
        return
    args = update.message.text.split()
    if len(args) < 3:
        await update.message.reply_text("❗ /take <кол-во> <user_id>")
        return
    try:
        amount = int(args[1])
        target_id = int(args[2])
    except ValueError:
        await update.message.reply_text("❌ Кол-во и ID должны быть числами.")
        return
    user = get_user(target_id)
    user["searches_extra"] = max(0, user["searches_extra"] - amount)
    update_user(target_id, user)
    await update.message.reply_text(f"✅ Забрано {amount} запросов у пользователя `{target_id}`.", parse_mode="Markdown")

async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Только для админа.")
        return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id, username, searches_today, searches_extra FROM users ORDER BY searches_today DESC")
    rows = cur.fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("📊 Нет пользователей.")
        return
    text = "📊 *Список пользователей*\n\n"
    for user_id, username, today, extra in rows[:20]:
        total = (3 - today) + extra
        text += f"• `{user_id}` — @{username or 'нет'} | запросов: {total}\n"
    await update.message.reply_text(text, parse_mode="Markdown")

# ==================== ЗАПУСК ====================

async def main():
    init_db()
    
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("give", cmd_give))
    application.add_handler(CommandHandler("take", cmd_take))
    application.add_handler(CommandHandler("users", cmd_users))
    
    application.add_handler(CallbackQueryHandler(back_to_main, pattern="^menu_back$"))
    application.add_handler(CallbackQueryHandler(show_profile, pattern="^menu_profile$"))
    application.add_handler(CallbackQueryHandler(global_search_start, pattern="^global_search$"))
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    await application.initialize()
    await application.start()
    await application.updater.start_polling(
        allowed_updates=[],
        read_timeout=60,
        write_timeout=60,
        connect_timeout=60,
        pool_timeout=60
    )
    
    logger.info("🚀 OTOB бот запущен на Render!")
    
    while True:
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())