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
ABSTRACT_API_KEY = "b2b0a9d6f8124c2f9b7a8d4f3e6c1a5b"
PHONE_VALIDATION_KEY = "07b3d5c4e8f2a1d6"
DEHASHED_KEY = "your_dehashed_key"  # Замени на свой (бесплатно)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== ИНИЦИАЛИЗАЦИЯ БОТА ====================
bot = telebot.TeleBot(TOKEN, parse_mode="Markdown")
bot.remove_webhook()

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

# ==================== ВСЕ OSINT-ИСТОЧНИКИ ====================

# ----- 1. Numverify -----
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
                            "valid": True,
                            "country": data.get('country_name'),
                            "location": data.get('location'),
                            "carrier": data.get('carrier'),
                            "line_type": data.get('line_type'),
                            "source": "numverify.com"
                        }
    except Exception as e:
        logger.error(f"Numverify error: {e}")
    return None

# ----- 2. Veriphone -----
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
                            "valid": True,
                            "country": data.get('country'),
                            "carrier": data.get('carrier'),
                            "type": data.get('phone_type'),
                            "source": "veriphone.io"
                        }
    except Exception as e:
        logger.error(f"Veriphone error: {e}")
    return None

# ----- 3. HTMLWeb.ru -----
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
                            "valid": True,
                            "country": data.get('country'),
                            "operator": data.get('operator'),
                            "region": data.get('region'),
                            "timezone": data.get('timezone'),
                            "source": "htmlweb.ru"
                        }
    except Exception as e:
        logger.error(f"HTMLWeb error: {e}")
    return None

# ----- 4. AbstractAPI -----
async def abstractapi_lookup(phone: str) -> dict:
    try:
        clean = re.sub(r'\D', '', phone)
        url = f"https://phonevalidation.abstractapi.com/v1/?api_key={ABSTRACT_API_KEY}&phone={clean}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('valid'):
                        return {
                            "valid": True,
                            "country": data.get('country', {}).get('name'),
                            "carrier": data.get('carrier'),
                            "location": data.get('location'),
                            "source": "abstractapi.com"
                        }
    except Exception as e:
        logger.error(f"AbstractAPI error: {e}")
    return None

# ----- 5. PhoneValidation -----
async def phonevalidation_lookup(phone: str) -> dict:
    try:
        clean = re.sub(r'\D', '', phone)
        url = f"https://api.phonevalidation.io/v1/validate?number={clean}&key={PHONE_VALIDATION_KEY}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('valid'):
                        return {
                            "valid": True,
                            "country": data.get('country'),
                            "carrier": data.get('carrier'),
                            "type": data.get('type'),
                            "source": "phonevalidation.io"
                        }
    except Exception as e:
        logger.error(f"PhoneValidation error: {e}")
    return None

# ----- 6. Hudson Rock (Новый - БЕЗ КЛЮЧА) -----
async def hudsonrock_lookup(query: str) -> dict:
    """Поиск в Hudson Rock (infostealer данные)"""
    try:
        if '@' in query:
            endpoint = f"https://cavalier.hudsonrock.com/api/v1/search-by-email?email={query}"
            params = None
        elif re.search(r'^\+?\d{10,15}$', re.sub(r'[\s\-()]', '', query)):
            phone = re.sub(r'\D', '', query)
            endpoint = f"https://cavalier.hudsonrock.com/api/v1/search-by-username?username={phone}"
            params = None
        else:
            endpoint = f"https://cavalier.hudsonrock.com/api/v1/search-by-domain?domain={query}"
            params = None
        
        async with aiohttp.ClientSession() as session:
            async with session.get(endpoint, timeout=15) as resp:
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

# ----- 7. ProxyNova (Новый) -----
async def proxynova_lookup(query: str) -> dict:
    """Поиск в ProxyNova"""
    try:
        # Используем публичный API ProxyNova
        url = f"https://api.proxynova.com/v1/search?q={query}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('results'):
                        return {
                            "source": "ProxyNova",
                            "found": True,
                            "total": len(data.get('results', [])),
                            "results": data.get('results', [])[:5]
                        }
    except Exception as e:
        logger.error(f"ProxyNova error: {e}")
    return None

# ----- 8. Leaker (CLI-инструмент) -----
async def leaker_lookup(query: str) -> str:
    """Поиск через Leaker (CLI)"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "leaker", query,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        return stdout.decode() if stdout else None
    except Exception as e:
        logger.error(f"Leaker error: {e}")
    return None

# ----- 9. Ola Osint (Python-инструмент) -----
async def olaosint_lookup(query: str) -> dict:
    """Поиск через Ola Osint (использует Holehe, HIBP, HudsonRock, Google)"""
    try:
        from ola_osint import OlaOsint
        
        ola = OlaOsint()
        results = {}
        
        if '@' in query:
            results = await ola.search_email(query)
        elif re.search(r'^\+?\d{10,15}$', re.sub(r'[\s\-()]', '', query)):
            results = await ola.search_phone(query)
        else:
            results = await ola.search_username(query)
        
        if results:
            return {
                "source": "OlaOsint",
                "found": True,
                "results": results
            }
    except Exception as e:
        logger.error(f"OlaOsint error: {e}")
    return None

# ----- 10. DeHashed -----
async def dehashed_lookup(query: str) -> dict:
    """Поиск в DeHashed"""
    try:
        url = f"https://dehashed.com/search?query={query}"
        headers = {"User-Agent": "Mozilla/5.0"}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    entries = soup.select('.entry, .result-item')
                    if entries:
                        results = []
                        for entry in entries[:5]:
                            text = entry.get_text(strip=True)
                            results.append(text[:200])
                        return {
                            "source": "DeHashed",
                            "found": True,
                            "total": len(entries),
                            "results": results
                        }
    except Exception as e:
        logger.error(f"DeHashed error: {e}")
    return None

# ----- 11. HaveIBeenPwned -----
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

# ----- 12. EmailRep -----
async def emailrep_lookup(email: str) -> dict:
    try:
        url = f"https://emailrep.io/{email}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "reputation": data.get('reputation'),
                        "suspicious": data.get('suspicious'),
                        "references": data.get('references', 0)
                    }
    except:
        pass
    return None

# ----- 13. HLR (smsc.ru) -----
async def hlr_lookup(phone: str) -> dict:
    try:
        clean = re.sub(r'\D', '', phone)
        url = f"https://smsc.ru/testhlr.php?phone={clean}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.text()
                    if 'OK' in data:
                        return {
                            "valid": True,
                            "status": "Активен",
                            "source": "smsc.ru"
                        }
                    else:
                        return {
                            "valid": False,
                            "status": "Не активен или ошибка",
                            "source": "smsc.ru"
                        }
    except Exception as e:
        logger.error(f"HLR error: {e}")
    return None

# ==================== ГЛОБАЛЬНЫЙ ПОИСК ====================

async def global_lookup(query: str) -> dict:
    query = query.strip()
    qtype = detect_query_type(query)
    
    result = {
        "query": query,
        "type": qtype,
        "timestamp": datetime.now().isoformat(),
        "sources": {}
    }
    
    # ===== ДЛЯ НОМЕРА =====
    if qtype == "phone":
        # API
        numverify = await numverify_lookup(query)
        if numverify:
            result["sources"]["numverify"] = numverify
        
        veriphone = await veriphone_lookup(query)
        if veriphone:
            result["sources"]["veriphone"] = veriphone
        
        htmlweb = await htmlweb_lookup(query)
        if htmlweb:
            result["sources"]["htmlweb"] = htmlweb
        
        abstractapi = await abstractapi_lookup(query)
        if abstractapi:
            result["sources"]["abstractapi"] = abstractapi
        
        phonevalidation = await phonevalidation_lookup(query)
        if phonevalidation:
            result["sources"]["phonevalidation"] = phonevalidation
        
        hlr = await hlr_lookup(query)
        if hlr:
            result["sources"]["hlr"] = hlr
        
        # OSINT-источники (новые)
        hudsonrock = await hudsonrock_lookup(query)
        if hudsonrock:
            result["sources"]["hudsonrock"] = hudsonrock
        
        proxynova = await proxynova_lookup(query)
        if proxynova:
            result["sources"]["proxynova"] = proxynova
        
        # CLI-инструменты (если установлены)
        leaker = await leaker_lookup(query)
        if leaker:
            result["sources"]["leaker"] = leaker
        
        olaosint = await olaosint_lookup(query)
        if olaosint:
            result["sources"]["olaosint"] = olaosint
        
        dehashed = await dehashed_lookup(query)
        if dehashed:
            result["sources"]["dehashed"] = dehashed
    
    # ===== ДЛЯ EMAIL =====
    if qtype == "email":
        hibp = await hibp_lookup(query)
        if hibp:
            result["sources"]["hibp"] = hibp
        
        emailrep = await emailrep_lookup(query)
        if emailrep:
            result["sources"]["emailrep"] = emailrep
        
        hudsonrock = await hudsonrock_lookup(query)
        if hudsonrock:
            result["sources"]["hudsonrock"] = hudsonrock
        
        proxynova = await proxynova_lookup(query)
        if proxynova:
            result["sources"]["proxynova"] = proxynova
        
        dehashed = await dehashed_lookup(query)
        if dehashed:
            result["sources"]["dehashed"] = dehashed
        
        olaosint = await olaosint_lookup(query)
        if olaosint:
            result["sources"]["olaosint"] = olaosint
    
    # ===== ДЛЯ USERNAME =====
    if qtype == "username":
        hudsonrock = await hudsonrock_lookup(query)
        if hudsonrock:
            result["sources"]["hudsonrock"] = hudsonrock
        
        proxynova = await proxynova_lookup(query)
        if proxynova:
            result["sources"]["proxynova"] = proxynova
        
        dehashed = await dehashed_lookup(query)
        if dehashed:
            result["sources"]["dehashed"] = dehashed
        
        olaosint = await olaosint_lookup(query)
        if olaosint:
            result["sources"]["olaosint"] = olaosint
    
    return result

# ==================== ФОРМАТИРОВАНИЕ РЕЗУЛЬТАТА ====================

def format_global_result(data: dict) -> str:
    query = data['query']
    qtype = data['type']
    sources = data.get("sources", {})
    
    reply = f"🔎 *OTOB — Osint Tool Olimpov Bot*\n\n"
    reply += f"📋 Тип: {qtype}\n"
    reply += f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n\n"
    
    if not sources:
        reply += "❌ Ничего не найдено.\n\n"
        reply += "💡 Попробуйте другой запрос."
        return reply
    
    # ===== ВЫВОД ПО НОМЕРУ =====
    if qtype == "phone":
        for source_name, data in sources.items():
            reply += f"🔹 **{source_name}**\n"
            if isinstance(data, dict):
                for key, value in data.items():
                    if key != "source" and value:
                        if isinstance(value, list):
                            reply += f"   {key}: {len(value)} результатов\n"
                            for item in value[:3]:
                                if isinstance(item, dict):
                                    reply += f"      • {item}\n"
                                else:
                                    reply += f"      • {item}\n"
                        else:
                            reply += f"   {key}: {value}\n"
            elif isinstance(data, list):
                reply += f"   {len(data)} результатов\n"
                for item in data[:5]:
                    reply += f"   • {item}\n"
            reply += "\n"
    
    # ===== ВЫВОД ПО EMAIL =====
    if qtype == "email":
        for source_name, data in sources.items():
            reply += f"🔹 **{source_name}**\n"
            if isinstance(data, dict):
                for key, value in data.items():
                    if key != "source" and value:
                        if isinstance(value, list):
                            reply += f"   {key}: {len(value)} результатов\n"
                            for item in value[:5]:
                                reply += f"      • {item}\n"
                        else:
                            reply += f"   {key}: {value}\n"
            elif isinstance(data, list):
                reply += f"   {len(data)} результатов\n"
                for item in data[:5]:
                    reply += f"   • {item}\n"
            reply += "\n"
    
    # ===== ВЫВОД ПО USERNAME =====
    if qtype == "username":
        for source_name, data in sources.items():
            reply += f"🔹 **{source_name}**\n"
            if isinstance(data, dict):
                for key, value in data.items():
                    if key != "source" and value:
                        if isinstance(value, list):
                            reply += f"   {key}: {len(value)} результатов\n"
                            for item in value[:5]:
                                reply += f"      • {item}\n"
                        else:
                            reply += f"   {key}: {value}\n"
            elif isinstance(data, list):
                reply += f"   {len(data)} результатов\n"
                for item in data[:5]:
                    reply += f"   • {item}\n"
            reply += "\n"
    
    return reply

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
        types.InlineKeyboardButton("📧 Email", callback_data="menu_email"),
        types.InlineKeyboardButton("📱 Телефон", callback_data="menu_phone")
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
        f"🔍 *OTOB — Osint Tool Olimpov Bot*\n"
        f"👨‍⚖️ *С поддержкой Мирослава Олипова*\n\n"
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
            f"🔍 *OTOB — Osint Tool Olimpov Bot*\n👨‍⚖️ *С поддержкой Мирослава Олипова*\n\n📌 *Выбери действие:*",
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
            "• 🌐 Глобальный поиск — номер, email, ФИО\n\n"
            "📌 *Поиск по данным:*\n"
            "• 📧 Email — проверка утечек\n"
            "• 📱 Телефон — оператор, регион\n\n"
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
            "• Отправь номер, email, никнейм или ФИО\n"
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
            "ℹ️ Бот использует 13+ OSINT-источников.",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("⬅️ Назад в меню", callback_data="menu_back")
            )
        )
        return
    
    if call.data in ["menu_email", "menu_phone"]:
        descriptions = {
            "menu_email": "📧 *Проверка email*\n\nОтправь email для проверки утечек.",
            "menu_phone": "📱 *Проверка телефона*\n\nОтправь номер (79261234567).",
        }
        bot.edit_message_text(
            descriptions.get(call.data, "Функция активна."),
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("⬅️ Назад в меню", callback_data="menu_back")
            )
        )
        return

# ==================== ОБРАБОТЧИК ТЕКСТА ====================

@bot.message_handler(func=lambda message: True)
def handle_text(message):
    text = message.text.strip()
    if not text or text.startswith('/'):
        return
    
    user_id = message.from_user.id
    
    if not can_search(user_id):
        bot.reply_to(message, "❌ *Лимит поисков исчерпан!*", parse_mode="Markdown")
        return
    
    msg = bot.reply_to(message, "⏳ Выполняется глобальный поиск...")
    
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        data = loop.run_until_complete(global_lookup(text))
        loop.close()
        
        reply = format_global_result(data)
        remaining = use_search(user_id)
        reply += f"\n\n🔍 1 поиск потрачено. Осталось: {remaining}/3"
        
        bot.edit_message_text(
            reply,
            message.chat.id,
            msg.message_id,
            parse_mode="Markdown",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("⬅️ Назад в меню", callback_data="menu_back")
            )
        )
        
    except Exception as e:
        bot.edit_message_text(
            f"⚠️ Ошибка: {str(e)[:100]}",
            message.chat.id,
            msg.message_id
        )

# ==================== ЗАПУСК ====================

if __name__ == "__main__":
    init_db()
    logger.info("🚀 OTOB бот запускается...")
    bot.remove_webhook()
    bot.infinity_polling(timeout=60, long_polling_timeout=30)
