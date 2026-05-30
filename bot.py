#!/usr/bin/env python3
import json
import logging
import asyncio
import os
import re
from datetime import datetime, time as dtime
from pathlib import Path

import aiohttp
from telegram import (
    Update, Bot, InlineKeyboardMarkup, InlineKeyboardButton,
    WebAppInfo, InputMediaPhoto
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)
from telegram.constants import ChatAction

BOT_TOKEN      = os.environ.get("BOT_TOKEN", "8968930119:AAE5p8Egja2ZMGo59QA6-Wywkeywnjstacc")
WEBAPP_URL     = os.environ.get("WEBAPP_URL", "")
DATA_FILE      = Path(__file__).parent / "data.json"
STEAM_FILE     = Path(__file__).parent / "steam_config.json"
CHECK_INTERVAL = 3 * 3600

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(Path(__file__).parent / "bot.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ── Хранилище ──────────────────────────────────────────────────────────────────

def load_data():
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    return {
        "chat_ids": [], "sent_ids": [], "claimed_ids": [],
        "wishlist": {}, "schedules": {}, "min_discount": {},
        "stats": {"total_sent": 0, "total_claimed": 0}
    }

def save_data(d):
    DATA_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2))

def load_steam():
    sess   = os.environ.get("STEAM_SESSION_ID")
    secure = os.environ.get("STEAM_LOGIN_SECURE")
    if sess and secure:
        return {"session_id": sess, "login_secure": secure}
    return json.loads(STEAM_FILE.read_text()) if STEAM_FILE.exists() else {}

def save_steam(c):
    STEAM_FILE.write_text(json.dumps(c, indent=2))

# ── Steam API ──────────────────────────────────────────────────────────────────

async def fetch_deals():
    url = "https://store.steampowered.com/api/featuredcategories/?cc=ru&l=russian"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                data = await r.json(content_type=None)
        deals = []
        for item in data.get("specials", {}).get("items", []):
            if item.get("discount_percent", 0) > 0:
                deals.append({
                    "id": item["id"],
                    "name": item.get("name", "?"),
                    "discount": item.get("discount_percent", 0),
                    "old_price": item.get("original_price", 0) / 100,
                    "new_price": item.get("final_price", 0) / 100,
                    "image": item.get("header_image", ""),
                    "expires": item.get("discount_expiration", 0),
                })
        return sorted(deals, key=lambda x: -x["discount"])
    except Exception as e:
        log.error(f"fetch_deals: {e}")
        return []


async def fetch_app_details(app_id: int) -> dict:
    url = f"https://store.steampowered.com/api/appdetails/?appids={app_id}&l=russian&cc=ru"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json(content_type=None)
        return data.get(str(app_id), {}).get("data", {})
    except Exception:
        return {}


async def search_steam(query: str) -> list:
    url = f"https://store.steampowered.com/search/results/?term={query}&json=1&count=6&cc=ru&l=russian"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json(content_type=None)
        results = []
        for item in data.get("items", []):
            price_str = item.get("price", "")
            discount = 0
            if isinstance(price_str, dict):
                discount = price_str.get("discount_pct", 0)
            results.append({
                "id": item.get("id"),
                "name": item.get("name", "?"),
                "discount": discount,
                "logo": item.get("logo", ""),
                "url": f"https://store.steampowered.com/app/{item.get('id')}",
            })
        return results
    except Exception:
        return []


async def get_package_id(app_id: int):
    info = await fetch_app_details(app_id)
    pkgs = info.get("packages", [])
    return pkgs[0] if pkgs else None


async def claim_free_game(app_id: int, session_id: str, login_secure: str):
    pkg = await get_package_id(app_id)
    if not pkg:
        return False, "Пакет не найден"
    cookies = {"sessionid": session_id, "steamLoginSecure": login_secure, "birthtime": "568022401"}
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": f"https://store.steampowered.com/app/{app_id}/",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/120.0.0.0",
    }
    try:
        async with aiohttp.ClientSession(cookies=cookies, headers=headers) as s:
            async with s.post(
                "https://store.steampowered.com/checkout/addfreelicense/",
                data=f"sessionid={session_id}&subid={pkg}&action=add_to_cart",
                timeout=aiohttp.ClientTimeout(total=15), allow_redirects=True
            ) as r:
                text = await r.text()
                if "already" in text.lower() or "owned" in text.lower():
                    return True, "уже в библиотеке"
                return r.status == 200, f"HTTP {r.status}"
    except Exception as e:
        return False, str(e)

# ── Форматирование ─────────────────────────────────────────────────────────────

def stars(score: int) -> str:
    if score >= 90: return "⭐⭐⭐⭐⭐"
    if score >= 75: return "⭐⭐⭐⭐"
    if score >= 60: return "⭐⭐⭐"
    if score >= 40: return "⭐⭐"
    return "⭐"

def discount_bar(pct: int) -> str:
    filled = round(pct / 10)
    return "█" * filled + "░" * (10 - filled)

def format_deal(deal: dict, info: dict = None) -> str:
    name    = deal["name"]
    pct     = deal["discount"]
    old_p   = deal["old_price"]
    new_p   = deal["new_price"]
    expires = deal.get("expires", 0)
    app_id  = deal["id"]

    if pct == 100:
        price_line = "💚 <b>БЕСПЛАТНО</b>"
        badge = "🆓 БЕСПЛАТНО"
    else:
        price_line = f"💰 <s>{old_p:.0f}₽</s> → <b>{new_p:.0f}₽</b>"
        badge = f"🔥 -{pct}%"

    bar = discount_bar(pct)
    exp_line = ""
    if expires:
        dt = datetime.fromtimestamp(expires)
        exp_line = f"\n⏰ <b>До:</b> {dt.strftime('%d.%m.%Y %H:%M')}"

    desc_line = ""
    review_line = ""
    if info:
        desc = info.get("short_description", "")
        if desc:
            desc_line = f"\n📝 {desc[:180]}{'...' if len(desc)>180 else ''}"
        rev = info.get("review_score", 0)
        rev_desc = info.get("review_score_desc", "")
        if rev_desc and rev_desc != "No user reviews":
            review_line = f"\n{stars(rev)} <i>{rev_desc}</i>"

    return (
        f"🎮 <b>{name}</b>\n"
        f"{badge}  <code>{bar}</code>\n"
        f"{price_line}"
        f"{exp_line}"
        f"{review_line}"
        f"{desc_line}\n"
        f"🔗 store.steampowered.com/app/{app_id}"
    )

def deal_keyboard(deal: dict, webapp_url: str = None) -> InlineKeyboardMarkup:
    app_id = deal["id"]
    url    = f"https://store.steampowered.com/app/{app_id}"
    buttons = [[InlineKeyboardButton("🛒 Открыть в Steam", url=url)]]
    if webapp_url:
        buttons.append([InlineKeyboardButton("🎮 Мини-приложение", web_app=WebAppInfo(webapp_url))])
    return InlineKeyboardMarkup(buttons)

# ── Отправка скидок ────────────────────────────────────────────────────────────

async def send_deals(bot: Bot, chat_id: int, deals: list, limit=10, fetch_info=False):
    sent = 0
    webapp_url = WEBAPP_URL or None

    for deal in deals[:limit]:
        if not deal.get("image"):
            continue
        try:
            info = await fetch_app_details(deal["id"]) if fetch_info else {}
            caption = format_deal(deal, info)
            kb = deal_keyboard(deal, webapp_url)
            await bot.send_photo(
                chat_id=chat_id, photo=deal["image"],
                caption=caption, parse_mode="HTML", reply_markup=kb
            )
            sent += 1
            await asyncio.sleep(0.4)
        except Exception as e:
            log.error(f"send_deals {deal['id']}: {e}")
    return sent

# ── Команды ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    d = load_data()
    is_new = chat_id not in d["chat_ids"]
    if is_new:
        d["chat_ids"].append(chat_id)
        save_data(d)

    await update.message.reply_text(
        "🎮 <b>Steam Deals Bot</b>\n\n"
        "Слежу за скидками 24/7 и автоматически добавляю бесплатные игры в твою библиотеку.\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "📨 <b>Авто-рассылка</b> — скидки ≥50% каждые 3 часа\n"
        "🆓 <b>100% скидки</b> — мгновенно в библиотеку\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Используй меню ниже ⬇️",
        parse_mode="HTML"
    )
    if is_new:
        await asyncio.sleep(0.5)
        await update.message.reply_text("🔍 Загружаю топовые скидки прямо сейчас...")
        await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_PHOTO)
        deals = await fetch_deals()
        top = [d for d in deals if d["discount"] >= 50][:5]
        if top:
            await send_deals(context.bot, chat_id, top, fetch_info=True)


async def cmd_deals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    d = load_data()
    min_pct = int(d.get("min_discount", {}).get(str(chat_id), 0))
    await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_PHOTO)
    await update.message.reply_text("🔍 Загружаю все скидки...")
    deals = await fetch_deals()
    filtered = [x for x in deals if x["discount"] >= min_pct]
    if not filtered:
        await update.message.reply_text("Скидок не найдено 😕")
        return
    sent = await send_deals(context.bot, chat_id, filtered, fetch_info=True)
    await update.message.reply_text(f"✅ Показано <b>{sent}</b> скидок", parse_mode="HTML")


async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_PHOTO)
    deals = await fetch_deals()
    if not deals:
        await update.message.reply_text("Не удалось загрузить скидки 😕")
        return
    await update.message.reply_text("🏆 <b>Топ-10 скидок прямо сейчас:</b>", parse_mode="HTML")
    await send_deals(context.bot, chat_id, deals[:10], fetch_info=True)


async def cmd_free(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_PHOTO)
    deals = await fetch_deals()
    free = [d for d in deals if d["discount"] == 100]
    if not free:
        await update.message.reply_text(
            "😔 Сейчас бесплатных игр нет.\n"
            "Как только появятся — сразу пришлю!"
        )
        return
    await update.message.reply_text(f"🆓 <b>Бесплатно прямо сейчас ({len(free)} шт.):</b>", parse_mode="HTML")
    await send_deals(context.bot, chat_id, free, fetch_info=True)


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text(
            "🔎 Использование: <code>/search Cyberpunk</code>",
            parse_mode="HTML"
        )
        return
    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    results = await search_steam(query)
    if not results:
        await update.message.reply_text(f"По запросу «{query}» ничего не найдено 😕")
        return
    lines = [f"🔎 <b>Результаты для «{query}»:</b>\n"]
    for r in results:
        disc = f" <b>(-{r['discount']}%)</b>" if r["discount"] else ""
        lines.append(f"• <a href=\"{r['url']}\">{r['name']}</a>{disc}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = load_data()
    total_users  = len(d.get("chat_ids", []))
    total_sent   = d.get("stats", {}).get("total_sent", 0)
    total_claimed = len(d.get("claimed_ids", []))
    deals = await fetch_deals()
    free_now = len([x for x in deals if x["discount"] == 100])
    big_now  = len([x for x in deals if x["discount"] >= 75])

    await update.message.reply_text(
        "📊 <b>Статистика бота</b>\n\n"
        f"👤 Пользователей: <b>{total_users}</b>\n"
        f"📨 Отправлено скидок: <b>{total_sent}</b>\n"
        f"🎁 Игр добавлено: <b>{total_claimed}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📦 Сейчас в Steam:\n"
        f"  🆓 Бесплатно: <b>{free_now}</b>\n"
        f"  🔥 Скидка 75%+: <b>{big_now}</b>\n"
        f"  💰 Всего скидок: <b>{len(deals)}</b>",
        parse_mode="HTML"
    )


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = load_data()
    claimed = d.get("claimed_ids", [])
    if not claimed:
        await update.message.reply_text("📭 Ещё ни одной игры не было добавлено автоматически.")
        return
    deals = await fetch_deals()
    deal_map = {str(x["id"]): x for x in deals}
    lines = [f"🎁 <b>Добавлено в библиотеку ({len(claimed)} игр):</b>\n"]
    for cid in claimed[-20:]:
        if cid in deal_map:
            g = deal_map[cid]
            lines.append(f"• <a href=\"https://store.steampowered.com/app/{g['id']}\">{g['name']}</a>")
        else:
            lines.append(f"• App {cid}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


async def cmd_wishlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    d = load_data()
    wl = d.setdefault("wishlist", {}).setdefault(str(chat_id), {})
    args = context.args

    if not args:
        if not wl:
            await update.message.reply_text(
                "📋 Список желаемого пуст.\n\n"
                "Добавь игру: <code>/wishlist add Cyberpunk 2077</code>\n"
                "Удалить: <code>/wishlist del Cyberpunk 2077</code>",
                parse_mode="HTML"
            )
            return
        lines = [f"📋 <b>Список желаемого ({len(wl)} игр):</b>\n"]
        for name, info in wl.items():
            price = f"  ${info.get('price', '?')}" if info.get("price") else ""
            lines.append(f"• {name}{price}")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        return

    action = args[0].lower()
    game = " ".join(args[1:])

    if action == "add" and game:
        results = await search_steam(game)
        if results:
            r = results[0]
            wl[r["name"]] = {"id": r["id"]}
            save_data(d)
            await update.message.reply_text(
                f"✅ <b>{r['name']}</b> добавлена в список желаемого!\n"
                f"Пришлю когда появится скидка.",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text("Игра не найдена 😕")

    elif action in ("del", "remove") and game:
        found = [k for k in wl if game.lower() in k.lower()]
        if found:
            del wl[found[0]]
            save_data(d)
            await update.message.reply_text(f"✅ <b>{found[0]}</b> удалена из списка.", parse_mode="HTML")
        else:
            await update.message.reply_text("Игра не найдена в списке 😕")

    elif action == "clear":
        wl.clear()
        save_data(d)
        await update.message.reply_text("✅ Список желаемого очищен.")


async def cmd_setdiscount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "⚙️ Использование: <code>/setdiscount 70</code>\n"
            "Бот будет присылать только скидки ≥ указанного процента.",
            parse_mode="HTML"
        )
        return
    pct = max(0, min(100, int(context.args[0])))
    d = load_data()
    d.setdefault("min_discount", {})[str(chat_id)] = pct
    save_data(d)
    await update.message.reply_text(
        f"✅ Минимальная скидка установлена: <b>{pct}%</b>\n"
        f"Буду присылать только скидки от {pct}%.",
        parse_mode="HTML"
    )


async def cmd_app(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = WEBAPP_URL
    if not url:
        await update.message.reply_text(
            "⚠️ Мини-приложение недоступно.\n\n"
            "Убедись что переменная <code>WEBAPP_URL</code> задана в настройках деплоя.",
            parse_mode="HTML"
        )
        return
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🎮 Открыть Steam Deals", web_app=WebAppInfo(url=url))
    ]])
    await update.message.reply_text(
        "🎮 <b>Steam Deals — Мини-приложение</b>\n\n"
        "Все скидки с фильтрами, поиском и таймерами прямо в Telegram.",
        parse_mode="HTML", reply_markup=kb
    )


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    d = load_data()
    if chat_id in d["chat_ids"]:
        d["chat_ids"].remove(chat_id)
        save_data(d)
    await update.message.reply_text("❌ Отписался от рассылки. Напиши /start чтобы вернуться.")


async def cmd_setsteam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = load_steam()
    if not context.args and cfg:
        await update.message.reply_text(
            "✅ <b>Steam аккаунт привязан</b>\n\n"
            "Игры со скидкой 100% добавляются автоматически.\n\n"
            "Чтобы обновить куки:\n"
            "<code>python3 ~/софты/steam\\ bot/get_steam_cookies.py</code>",
            parse_mode="HTML"
        )
        return
    if not context.args:
        await update.message.reply_text(
            "🔑 Steam не привязан.\n\n"
            "Запусти в терминале:\n"
            "<code>python3 ~/софты/steam\\ bot/get_steam_cookies.py</code>\n\n"
            "Откроется браузер → войди → нажми Enter. Готово!",
            parse_mode="HTML"
        )
        return
    if len(context.args) == 2:
        save_steam({"session_id": context.args[0], "login_secure": context.args[1]})
        await update.message.reply_text("✅ Steam привязан! Игры с 100% скидкой добавляются автоматически.")

# ── Авто-проверка ──────────────────────────────────────────────────────────────

async def periodic_check(context: ContextTypes.DEFAULT_TYPE):
    d = load_data()
    if not d["chat_ids"]:
        return
    log.info("Проверяю скидки...")
    deals = await fetch_deals()
    if not deals:
        return

    steam_cfg = load_steam()
    sent_ids   = set(d.get("sent_ids", []))
    claimed_ids = set(d.get("claimed_ids", []))
    stats = d.setdefault("stats", {"total_sent": 0, "total_claimed": 0})

    # Бесплатные → добавляем на аккаунт
    if steam_cfg:
        for game in [x for x in deals if x["discount"] == 100 and str(x["id"]) not in claimed_ids]:
            ok, msg = await claim_free_game(game["id"], steam_cfg["session_id"], steam_cfg["login_secure"])
            if ok:
                claimed_ids.add(str(game["id"]))
                stats["total_claimed"] = stats.get("total_claimed", 0) + 1
                for cid in d["chat_ids"]:
                    try:
                        await context.bot.send_photo(
                            chat_id=cid,
                            photo=game["image"],
                            caption=(
                                f"🎁 <b>Добавлено в библиотеку!</b>\n\n"
                                f"🎮 <b>{game['name']}</b>\n"
                                f"🆓 Получено бесплатно автоматически\n\n"
                                f"🔗 store.steampowered.com/app/{game['id']}"
                            ),
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass

    # Скидки ≥50% → уведомляем
    new_deals = [x for x in deals if x["discount"] >= 50 and str(x["id"]) not in sent_ids]
    if new_deals:
        log.info(f"Новых скидок: {len(new_deals)}")
        for cid in d["chat_ids"]:
            min_pct = int(d.get("min_discount", {}).get(str(cid), 50))
            to_send = [x for x in new_deals if x["discount"] >= min_pct]
            cnt = await send_deals(context.bot, cid, to_send, fetch_info=True)
            stats["total_sent"] = stats.get("total_sent", 0) + cnt

    # Вишлист — проверяем совпадения
    wl_map = d.get("wishlist", {})
    deal_names = {x["name"].lower(): x for x in deals}
    for cid, wl in wl_map.items():
        for wname, winfo in wl.items():
            for dname, deal in deal_names.items():
                if wname.lower() in dname and str(deal["id"]) not in sent_ids:
                    try:
                        await context.bot.send_photo(
                            chat_id=int(cid),
                            photo=deal["image"],
                            caption=(
                                f"🔔 <b>Скидка на игру из вишлиста!</b>\n\n"
                                + format_deal(deal)
                            ),
                            parse_mode="HTML",
                            reply_markup=deal_keyboard(deal)
                        )
                    except Exception:
                        pass

    sent_ids.update(str(x["id"]) for x in deals)
    d["sent_ids"]    = list(sent_ids)
    d["claimed_ids"] = list(claimed_ids)
    d["stats"]       = stats
    save_data(d)

# ── Запуск ─────────────────────────────────────────────────────────────────────

async def post_init(app: Application):
    cfg = load_steam()
    steam_label = "✅ Steam привязан" if cfg else "🔑 Привязать Steam"
    await app.bot.set_my_commands([
        ("start",       "🚀 Запустить / главное меню"),
        ("app",         "🎮 Открыть мини-приложение"),
        ("top",         "🏆 Топ-10 скидок сейчас"),
        ("free",        "🆓 Бесплатные игры сейчас"),
        ("deals",       "🔥 Все текущие скидки"),
        ("search",      "🔎 Найти игру"),
        ("wishlist",    "📋 Список желаемого"),
        ("stats",       "📊 Статистика"),
        ("history",     "🎁 История полученных игр"),
        ("setdiscount", "⚙️ Мин. процент скидки"),
        ("setsteam",    steam_label),
        ("stop",        "❌ Отписаться"),
    ])
    from telegram import MenuButtonCommands
    await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())


def main():
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    for cmd, handler in [
        ("start", cmd_start), ("deals", cmd_deals), ("top", cmd_top),
        ("free", cmd_free), ("search", cmd_search), ("stats", cmd_stats),
        ("history", cmd_history), ("wishlist", cmd_wishlist),
        ("setdiscount", cmd_setdiscount), ("app", cmd_app),
        ("stop", cmd_stop), ("setsteam", cmd_setsteam),
    ]:
        application.add_handler(CommandHandler(cmd, handler))

    application.job_queue.run_repeating(periodic_check, interval=CHECK_INTERVAL, first=30)
    log.info("Бот запущен.")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
