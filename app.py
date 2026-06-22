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

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TOKEN", "8950707948:AAHmqsd7zHKXZ56SmYPwCtHkqMnXHfjhTWU")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "8545020464"))
DB_PATH = os.path.join("/tmp", "otob_bot.db")

# ===== КЛЮЧИ API =====
NUMVERIFY_KEY = "9b8695be8a2fff21a10445d9d4e99469"
VERIPHONE_KEY = "ok_382cdf7065b120448d12a80c7e975756"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== АВТОУСТАНОВКА ИНСТРУМЕНТОВ ====================

def install_package(package: str):
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package, "--quiet"])
        logger.info(f"✅ Установлен: {package}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка установки {package}: {e}")
        return False

def install_osint_mcp():
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "osint-mcp", "--quiet"])
        logger.info("✅ Установлен: osint-mcp")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка установки osint-mcp: {e}")
        return False

def setup_tools():
    logger.info("🔧 Проверка и установка инструментов...")
    try:
        import osint_mcp
        logger.info("✅ osint-mcp уже установлен")
    except ImportError:
        logger.info("📦 Устанавливаю osint-mcp...")
        install_osint_mcp()
    
    tools = ["sherlock", "maigret", "theHarvester", "dnstwist", "holehe"]
    for tool in tools:
        try:
            subprocess.check_call([tool, "--help"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logger.info(f"✅ {tool} уже установлен")
        except (subprocess.CalledProcessError, FileNotFoundError):
            logger.info(f"📦 Устанавливаю {tool}...")
            try:
                subprocess.check_call(["pip", "install", tool, "--quiet"])
            except:
                pass
    
    logger.info("✅ Все инструменты проверены!")
    return True

# ==================== ИНИЦИАЛИЗАЦИЯ БОТА ====================
bot = telebot.TeleBot(TOKEN, parse_mode="Markdown")
bot.remove_webhook()

# Хранилище результатов для HTML-отчётов
user_results = {}

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

async def veriphone_lookup(phone: str) -> dict:
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

async def numverify_lookup(phone: str) -> dict:
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

async def holehe_lookup(email: str) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "holehe", email, "--only-used",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        return stdout.decode() if stdout else None
    except Exception as e:
        logger.error(f"Holehe error: {e}")
    return None

async def sherlock_lookup(username: str) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "sherlock", username,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        return stdout.decode() if stdout else None
    except Exception as e:
        logger.error(f"Sherlock error: {e}")
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
    
    # ===== ДЛЯ НОМЕРА =====
    if qtype == "phone":
        htmlweb = await htmlweb_lookup(query)
        if htmlweb:
            result["sources"]["htmlweb"] = htmlweb
            total += 1
        
        veriphone = await veriphone_lookup(query)
        if veriphone:
            result["sources"]["veriphone"] = veriphone
            total += 1
        
        numverify = await numverify_lookup(query)
        if numverify:
            result["sources"]["numverify"] = numverify
            total += 1
        
        hlr = await hlr_lookup(query)
        if hlr:
            result["sources"]["hlr"] = hlr
            total += 1
    
    # ===== ДЛЯ EMAIL =====
    if qtype == "email":
        hibp = await hibp_lookup(query)
        if hibp:
            result["sources"]["hibp"] = hibp
            total += len(hibp)
        
        emailrep = await emailrep_lookup(query)
        if emailrep:
            result["sources"]["emailrep"] = emailrep
            total += 1
        
        holehe = await holehe_lookup(query)
        if holehe:
            result["sources"]["holehe"] = holehe
            total += 1
    
    # ===== ДЛЯ USERNAME =====
    if qtype == "username":
        sherlock = await sherlock_lookup(query)
        if sherlock:
            result["sources"]["sherlock"] = sherlock
            total += 1
    
    # ===== ДЛЯ IP =====
    if qtype == "ip":
        try:
            url = f"http://ip-api.com/json/{query}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get('status') == 'success':
                            result["sources"]["geoip"] = {
                                "country": data.get('country', '—'),
                                "city": data.get('city', '—'),
                                "region": data.get('region', '—'),
                                "isp": data.get('isp', '—'),
                                "asn": data.get('as', '—')
                            }
                            total += 1
        except Exception as e:
            logger.error(f"GeoIP error: {e}")
    
    # ===== ДЛЯ ВСЕХ ТИПОВ =====
    web_results = await duckduckgo_search(query)
    if web_results:
        result["sources"]["web"] = web_results
        total += len(web_results)
    
    result["total_results"] = total
    return result

# ==================== ГЕНЕРАЦИЯ HTML-ОТЧЁТА ====================

def generate_html_report(query: str, data: dict) -> str:
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
    
    html = f"""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OTOB — Osint Tool Olimpov Bot</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ background: #0d0d0d; color: #b0b0b0; font-family: 'Segoe UI', sans-serif; padding: 30px 20px; line-height: 1.6; }}
        .container {{ max-width: 1000px; margin: 0 auto; background: #161616; border-radius: 10px; padding: 30px 35px; border: 1px solid #2a2a2a; }}
        .header {{ border-bottom: 1px solid #2a2a2a; padding-bottom: 18px; margin-bottom: 22px; display: flex; justify-content: space-between; flex-wrap: wrap; }}
        .header h1 {{ font-size: 24px; font-weight: 600; color: #c8c8c8; }}
        .header h1 span {{ color: #6a6a6a; }}
        .header .sub {{ color: #6a6a6a; font-size: 13px; }}
        .badge {{ display: inline-block; background: #222222; padding: 3px 12px; border-radius: 4px; font-size: 12px; color: #8a8a8a; border: 1px solid #333333; }}
        .badge-success {{ background: #1a2a1a; color: #7aaa7a; border-color: #2a3a2a; }}
        .result-item {{ margin: 12px 0; padding: 14px 18px; background: #121212; border-radius: 6px; border-left: 3px solid #2a2a2a; }}
        .result-item .title {{ font-size: 16px; font-weight: 500; color: #c0c0c0; }}
        .result-item .text {{ font-size: 14px; color: #8a8a8a; margin-top: 6px; }}
        .result-item .extra {{ font-size: 13px; color: #6a6a6a; margin-top: 4px; }}
        .result-item .index {{ display: inline-block; background: #1a1a1a; color: #5a5a5a; font-size: 12px; padding: 1px 10px; border-radius: 4px; margin-right: 10px; }}
        .source-tag {{ display: inline-block; background: #1a1a1a; color: #5a5a5a; font-size: 10px; padding: 1px 8px; border-radius: 3px; margin-left: 10px; border: 1px solid #262626; }}
        .empty {{ color: #555555; font-style: italic; font-size: 14px; padding: 20px; text-align: center; }}
        .stats {{ margin-top: 20px; padding: 12px 18px; background: #121212; border-radius: 6px; border: 1px solid #1a1a1a; color: #6a6a6a; font-size: 13px; text-align: center; }}
        .footer {{ margin-top: 25px; padding-top: 16px; border-top: 1px solid #1e1e1e; font-size: 12px; color: #4a4a4a; text-align: center; }}
        .footer a {{ color: #6a6a6a; text-decoration: none; }}
        .watermark {{ position: fixed; bottom: 30px; left: 30px; z-index: 1000; opacity: 0.15; user-select: none; pointer-events: none; display: flex; flex-direction: column; align-items: center; }}
        .watermark svg {{ width: 80px; height: 80px; }}
        .watermark .text {{ color: #3a3a3a; font-size: 14px; font-weight: 700; letter-spacing: 3px; margin-top: 4px; text-transform: uppercase; }}
        @media (max-width: 600px) {{ .container {{ padding: 16px; }} .header h1 {{ font-size: 20px; }} .watermark svg {{ width: 50px; height: 50px; }} .watermark .text {{ font-size: 10px; }} }}
    </style>
</head>
<body>
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
    
    # ===== ГЛОБАЛЬНЫЙ ПОИСК =====
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
            "ℹ️ Бот использует 10+ OSINT-источников.",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("⬅️ Назад в меню", callback_data="menu_back")
            )
        )
        return
    
    # ===== ПОИСК ПО EMAIL =====
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
    
    # ===== ПОИСК ПО ТЕЛЕФОНУ =====
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
    
    # ===== ПОИСК ПО USERNAME =====
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

# ==================== КНОПКА ДЛЯ HTML-ОТЧЁТА ====================

@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("html_"))
def html_callback(call):
    bot.answer_callback_query(call.id)
    
    chat_id = call.message.chat.id
    user_id = call.from_user.id
    
    if chat_id not in user_results:
        bot.send_message(chat_id, "❌ Данные не найдены. Повторите поиск.")
        return
    
    query = user_results[chat_id]["query"]
    data = user_results[chat_id]["data"]
    
    html_content = generate_html_report(query, data)
    filename = f"otob_report_{user_id}_{int(datetime.now().timestamp())}.html"
    
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html_content)
    
    with open(filename, "rb") as f:
        bot.send_document(
            chat_id,
            f,
            caption=f"📄 *OTOB — Osint Tool Olimpov Bot*\n\n"
                    f"🔍 Запрос: `{query}`\n"
                    f"📊 Найдено: {data.get('total_results', 0)} результатов\n\n"
                    f"💡 Скачайте и откройте в браузере.",
            parse_mode="Markdown"
        )
    
    os.remove(filename)

# ==================== ОБРАБОТЧИК ТЕКСТА (ГЛАВНАЯ ФУНКЦИЯ) ====================

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
        
        # Сохраняем результаты для HTML-отчёта
        user_results[chat_id] = {"query": text, "data": data}
        
        # Отправляем краткое сообщение с кнопкой
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("📄 Скачать HTML-отчёт", callback_data=f"html_{chat_id}"),
            types.InlineKeyboardButton("⬅️ Назад в меню", callback_data="menu_back")
        )
        
        bot.edit_message_text(
            f"✅ *Поиск завершён!*\n\n"
            f"🔍 Запрос: `{text}`\n"
            f"📊 Найдено: **{total}** результатов\n"
            f"🔍 Осталось поисков: **{remaining}/3**\n\n"
            f"📄 Нажмите кнопку ниже, чтобы скачать полный отчёт в формате HTML.",
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
    setup_tools()
    init_db()
    logger.info("🚀 OTOB бот запускается...")
    bot.remove_webhook()
    bot.infinity_polling(timeout=60, long_polling_timeout=30)
