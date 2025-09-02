import re
import os
import logging
from datetime import datetime, timedelta
from uuid import uuid4

from dateutil import parser as dateparser
from apscheduler.schedulers.background import BackgroundScheduler

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

TOKEN = os.getenv("TG_BOT_TOKEN")  # положи токен в переменные окружения

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Память в рантайме (можно заменить на sqlite/json)
GIVEAWAYS = {}  # id -> dict
scheduler = BackgroundScheduler(timezone="Europe/Kyiv")
scheduler.start()

KEYWORDS = [
    r"\bgiveaway\b", r"\bрозыгрыш\b", r"\bgift(s)?\b", r"\bstar(s)?\b", r"\bприз\b",
    r"\bпортал(с)?\b", r"\bportals\b", r"\bтон\b", r"\bton\b"
]

URL_REGEX = r"(https?://[^\s]+)"
DEADLINE_HINTS = [r"до\s+(\d{1,2}[:.]\d{2})", r"deadline[:\s]+([^\n]+)", r"заканч[^\n]*?(\d{1,2}\s+\w+|\d{1,2}[:.]\d{2})"]

def extract_links(text: str):
    return re.findall(URL_REGEX, text or "", flags=re.IGNORECASE)

def matches_keywords(text: str):
    t = text.lower() if text else ""
    return any(re.search(pat, t) for pat in KEYWORDS)

def extract_deadline(text: str):
    if not text: return None
    # пробуем выцепить дату/время
    # 1) явная дата/время
    try:
        dt = dateparser.parse(text, dayfirst=True, fuzzy=True)
        if dt and dt > datetime.now(dt.tzinfo or None):
            return dt
    except Exception:
        pass
    # 2) эвристики «до 18:00», «deadline: завтра 21:00»
    for pat in DEADLINE_HINTS:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            try:
                return dateparser.parse(m.group(1), dayfirst=True, fuzzy=True)
            except Exception:
                continue
    return None

def build_buttons(links, gid, deadline=None):
    rows = []
    for url in links[:4]:
        rows.append([InlineKeyboardButton(text="Открыть ссылку", url=url)])
    # напоминания
    remind_row = [
        InlineKeyboardButton("Напомнить за 10 мин", callback_data=f"remind:{gid}:10"),
        InlineKeyboardButton("за 1 час", callback_data=f"remind:{gid}:60"),
    ]
    rows.append(remind_row)
    rows.append([InlineKeyboardButton("В архив", callback_data=f"archive:{gid}")])
    return InlineKeyboardMarkup(rows)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Пересылай мне посты/сообщения о розыгрышах (Portals, Stars и т.п.). "
        "Я вытащу ссылки и предложу кнопки для быстрого входа + смогу напомнить перед дедлайном."
    )

async def handle_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    text = msg.text or msg.caption or ""
    links = extract_links(text)
    is_giveaway = matches_keywords(text) or bool(links)

    if not is_giveaway:
        await msg.reply_text("Не похоже на розыгрыш. Если всё же он — пришли сообщение с ссылкой/условиями.")
        return

    gid = str(uuid4())[:8]
    deadline = extract_deadline(text)
    GIVEAWAYS[gid] = {
        "from_chat": (msg.forward_from_chat.title if msg.forward_from_chat else None),
        "text": text,
        "links": links,
        "deadline": deadline,
        "archived": False
    }

    title = f"🎁 Розыгрыш ({GIVEAWAYS[gid]['from_chat'] or 'переслано'})\nID: {gid}"
    if deadline:
        title += f"\nДедлайн: {deadline}"

    await msg.reply_text(
        title,
        reply_markup=build_buttons(links, gid, deadline)
    )

def schedule_reminder(chat_id, gid, minutes, application):
    when = datetime.now() + timedelta(minutes=minutes)
    def job():
        g = GIVEAWAYS.get(gid)
        if not g or g.get("archived"): return
        text = f"⏰ Напоминание по розыгрышу {gid}"
        buttons = build_buttons(g.get("links", []), gid, g.get("deadline"))
        application.create_task(application.bot.send_message(chat_id=chat_id, text=text, reply_markup=buttons))
    scheduler.add_job(job, "date", run_date=when)

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data.startswith("remind:"):
        _, gid, mins = data.split(":")
        schedule_reminder(q.message.chat_id, gid, int(mins), context.application)
        await q.edit_message_text(f"Напоминание поставлено через {mins} мин. (ID: {gid})")
    elif data.startswith("archive:"):
        _, gid = data.split(":")
        if gid in GIVEAWAYS: GIVEAWAYS[gid]["archived"] = True
        await q.edit_message_text(f"Розыгрыш {gid} отправлен в архив.")

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    active = [ (gid, g) for gid, g in GIVEAWAYS.items() if not g.get("archived") ]
    if not active:
        await update.message.reply_text("Сейчас нет активных карточек.")
        return
    lines = []
    for gid, g in active[:20]:
        lines.append(f"• {gid}: { (g['from_chat'] or 'unknown') } | links: {len(g['links'])} | deadline: {g['deadline']}")
    await update.message.reply_text("Активные:\n" + "\n".join(lines))

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.UpdateType.MESSAGE & (filters.TEXT | filters.Caption) , handle_forward))
    app.run_polling()

if __name__ == "__main__":
    main()
