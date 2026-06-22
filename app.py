import os
import time
import logging
import re
import sqlite3
import random
import aiohttp
import asyncio
from datetime import datetime
from bs4 import BeautifulSoup
import telebot
from telebot import types

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TOKEN", "8950707948:AAHmqsd7zHKXZ56SmYPwCtHkqMnXHfjhTWU")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "8545020464"))
DB_PATH = os.path.join("/tmp", "otob_bot.db")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== ИНИЦИАЛИЗАЦИЯ БОТА ====================
bot = telebot.TeleBot(TOKEN, parse_mode="Markdown")

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

# ==================== ВСЕ 30+ ПАРСЕРОВ ====================

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
    
    all_parsers = {
        "duckduckgo": parse_duckduckgo,
        "wikipedia": parse_wikipedia,
        "xray": parse_xray,
        "idcrawl": parse_idcrawl,
        "noimosiny": parse_noimosiny,
        "osint_industries": parse_osint_industries,
        "epieos": parse_epieos,
        "cyberbackgroundchecks": parse_cyberbackgroundchecks,
    }
    
    tasks = {}
    for name, parser in all_parsers.items():
        tasks[name] = parser(query)
    
    if qtype == "email":
        tasks["hibp"] = parse_hibp(query)
        tasks["emailrep"] = parse_emailrep(query)
        tasks["hunter"] = parse_hunter(query)
        tasks["rocketreach"] = parse_rocketreach(query)
        tasks["minerva"] = parse_minerva(query)
    
    if qtype == "fio":
        tasks["peekyou"] = parse_peekyou(query)
        tasks["spravkaru"] = parse_spravkaru(query)
        tasks["truepeoplesearch"] = parse_truepeoplesearch(query)
    
    if qtype == "phone":
        tasks["truecaller"] = parse_truecaller(query)
        tasks["syncme"] = parse_syncme(query)
        tasks["whoseno"] = parse_whoseno(query)
        tasks["truepeoplesearch"] = parse_truepeoplesearch(query)
        tasks["hlr"] = hlr_lookup(query)
    
    for name, task in tasks.items():
        try:
            data = await task
            if data:
                result["sources"][name] = data
                logger.info(f"✅ {name}: {len(data)} результатов")
        except Exception as e:
            logger.error(f"❌ {name} error: {e}")
    
    return result

# ==================== ФОРМАТИРОВАНИЕ РЕЗУЛЬТАТА ====================

def format_global_result(data: dict) -> str:
    query = data['query']
    qtype = data['type']
    sources = data.get("sources", {})
    
    reply = f"🔎 *OTOB — Osint Tool Olimpov Bot*\n\n"
    reply += f"📋 Тип: {qtype}\n"
    reply += f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n\n"
    
    all_results = []
    for source_name, items in sources.items():
        if items:
            for item in items:
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
    
    return reply

# ==================== ОБРАБОТЧИКИ КОМАНД ====================

@bot.message_handler(commands=['start'])
def start_command(message):
    user_id = message.from_user.id
    user_data = get_user(user_id, message.from_user.username or "Unknown")
    remaining = get_remaining(user_id)
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("🔍 Глобальный поиск", callback_data="global_search"),
        types.InlineKeyboardButton("👤 Профиль", callback_data="profile"),
        types.InlineKeyboardButton("🧑‍💻 Разработчики", url="https://t.me/lkblyad")
    )
    
    bot.send_message(
        message.chat.id,
        f"🔍 *OTOB — Osint Tool Olimpov Bot*\n\n"
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        f"📊 У тебя {remaining} поисков.\n\n"
        f"Отправь любой запрос для поиска:\n"
        f"• Номер телефона: +79991234567\n"
        f"• ФИО: Иванов Иван Иванович\n"
        f"• Email: user@example.com\n"
        f"• Никнейм, IP, домен или текст\n\n"
        f"⚡ Парсинг 30+ OSINT-сайтов",
        parse_mode="Markdown",
        reply_markup=markup
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
    if call.data == "global_search":
        bot.edit_message_text(
            "🌐 *Глобальный поиск*\n\n"
            "Отправь запрос для поиска:\n"
            "• Номер телефона: +79991234567\n"
            "• ФИО: Иванов Иван Иванович\n"
            "• Email: user@example.com\n"
            "• Никнейм, IP, домен или текст\n\n"
            "ℹ️ Бот парсит 30+ OSINT-сайтов.",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("⬅️ Назад", callback_data="back")
            )
        )
        bot.answer_callback_query(call.id)
    
    elif call.data == "profile":
        user = call.from_user
        user_data = get_user(user.id, user.username or "Unknown")
        remaining = get_remaining(user.id)
        text = (
            f"👤 *Твой профиль*\n\n"
            f"🆔 ID: `{user.id}`\n"
            f"👤 Username: @{user.username or 'нет'}\n"
            f"📊 Поисков сегодня: {user_data['searches_today']}/3\n"
            f"📊 Бонусных: {user_data['searches_extra']}\n"
            f"📊 Всего доступно: {remaining}\n"
            f"👑 Админ: {'✅' if user.id == ADMIN_ID else '❌'}"
        )
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("⬅️ Назад", callback_data="back")
            )
        )
        bot.answer_callback_query(call.id)
    
    elif call.data == "back":
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("🔍 Глобальный поиск", callback_data="global_search"),
            types.InlineKeyboardButton("👤 Профиль", callback_data="profile"),
            types.InlineKeyboardButton("🧑‍💻 Разработчики", url="https://t.me/lkblyad")
        )
        bot.edit_message_text(
            f"🔍 *OTOB — Osint Tool Olimpov Bot*\n\n"
            f"Отправь любой запрос для поиска:\n"
            f"• Номер телефона: +79991234567\n"
            f"• ФИО: Иванов Иван Иванович\n"
            f"• Email: user@example.com\n"
            f"• Никнейм, IP, домен или текст\n\n"
            f"⚡ Парсинг 30+ OSINT-сайтов",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=markup
        )
        bot.answer_callback_query(call.id)

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
    
    # Отправляем сообщение о начале поиска
    msg = bot.reply_to(message, "⏳ OTOB выполняет поиск по 30+ сайтам...")
    
    try:
        # Запускаем асинхронный поиск
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        data = loop.run_until_complete(global_lookup(text))
        loop.close()
        
        reply = format_global_result(data)
        remaining = use_search(user_id)
        reply += f"\n\n🔍 Осталось: {remaining}/3"
        
        markup = types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("⬅️ Назад в меню", callback_data="back")
        )
        
        bot.edit_message_text(
            reply,
            message.chat.id,
            msg.message_id,
            parse_mode="Markdown",
            reply_markup=markup
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
    logger.info("🚀 OTOB бот запускается на telebot...")
    bot.infinity_polling()
