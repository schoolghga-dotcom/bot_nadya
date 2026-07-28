"""
Бот-переписка: дело Нади.
Сценарий по документу. Запуск: /start
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update, User
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

PAUSE = 1.4
ADMIN_CHAT_ID = -1003942339628
STATS_FILE = Path(__file__).resolve().parent / "stats.json"

INSTAGRAM_URL = "https://instagram.com/nadyagotmarried"
TELEGRAM_URL = "https://t.me/nadyagotmarried"
PLANETA_URL = "https://planeta.ru/campaigns/nadyagotmarried"

GENDER_LABELS = {
    "male": "парень",
    "female": "девушка",
    "unknown": "не указан",
}

ADVICE_LABELS = {
    "a": "вариант А",
    "b": "вариант Б",
}


def advice_reply(gender: str, choice: str) -> str:
    if choice == "a":
        return "Спасибо за поддержку, но что-то не получается…"
    if gender == "male":
        return "Может, ты и прав("
    return "Ты говоришь, как моя подруга Лена!"


def load_stats() -> dict[str, Any]:
    if not STATS_FILE.exists():
        return {"users": {}}
    try:
        return json.loads(STATS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"users": {}}


def save_stats(data: dict[str, Any]) -> None:
    STATS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def user_key(user_id: int) -> str:
    return str(user_id)


def ensure_user(data: dict[str, Any], user: User) -> dict[str, Any]:
    key = user_key(user.id)
    users = data.setdefault("users", {})
    if key not in users:
        users[key] = {
            "user_id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "gender": None,
            "advice_choice": None,
            "starts": 0,
            "restarts": 0,
            "completed": 0,
            "first_seen": datetime.now(timezone.utc).isoformat(),
            "last_seen": datetime.now(timezone.utc).isoformat(),
        }
    else:
        users[key]["username"] = user.username
        users[key]["first_name"] = user.first_name
        users[key]["last_name"] = user.last_name
        users[key]["last_seen"] = datetime.now(timezone.utc).isoformat()
    return users[key]


def summary_text(data: dict[str, Any]) -> str:
    users = list(data.get("users", {}).values())
    total = len(users)
    male = sum(1 for u in users if u.get("gender") == "male")
    female = sum(1 for u in users if u.get("gender") == "female")
    unknown = sum(1 for u in users if u.get("gender") in (None, "unknown"))
    a = sum(1 for u in users if u.get("advice_choice") == "a")
    b = sum(1 for u in users if u.get("advice_choice") == "b")
    completed = sum(int(u.get("completed") or 0) for u in users)
    return (
        f"Всего пользователей: {total}\n"
        f"Пол: ♂ {male} / ♀ {female} / ? {unknown}\n"
        f"Ответы: А — {a}, Б — {b}\n"
        f"Дошли до финала: {completed}"
    )


def format_user(user: User) -> str:
    name = user.full_name or "—"
    uname = f"@{user.username}" if user.username else "без username"
    return f"{name} ({uname}, id {user.id})"


def get_gender(record: dict[str, Any], context: ContextTypes.DEFAULT_TYPE) -> str:
    return record.get("gender") or context.user_data.get("gender") or "female"


async def notify_admin(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    try:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=text)
    except Exception:
        logger.exception("Не удалось отправить статистику в админ-чат")


# ---------------------------------------------------------------------------
# Вспомогательные функции для отслеживания и очистки сообщений
# ---------------------------------------------------------------------------

def track_msg(context: ContextTypes.DEFAULT_TYPE, msg_id: int) -> None:
    """Добавляет ID сообщения в список для последующей очистки."""
    context.user_data.setdefault("msg_ids", []).append(msg_id)


async def clear_chat(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int
) -> None:
    """Удаляет все отслеживаемые сообщения и сбрасывает список."""
    msg_ids: list[int] = context.user_data.pop("msg_ids", [])
    for mid in msg_ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=mid)
        except Exception:
            pass  # сообщение уже удалено или недоступно




async def send_as_chat(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    pause: float = PAUSE,
) -> Message:
    await asyncio.sleep(pause)
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    typing = min(2.2, 0.55 + len(text) * 0.012)
    await asyncio.sleep(typing)
    return await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )


async def ask_gender(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Девушка", callback_data="gender_female"),
                InlineKeyboardButton("Парень", callback_data="gender_male"),
            ]
        ]
    )
    msg = await send_as_chat(
        context,
        chat_id,
        "Подожди секунду.\n"
        "\n"
        "Мне важно знать, кто читает это — "
        "иначе я буду говорить в пустоту.\n"
        "\n"
        "Кто ты?",
        reply_markup=keyboard,
        pause=0.7,
    )
    track_msg(context, msg.message_id)


async def send_story(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    gender: str,
) -> None:
    msg = await send_as_chat(
        context,
        chat_id,
        "Окей. Буду говорить с тобой без лишней осторожности, без прелюдий.",
        pause=0.6,
    )
    track_msg(context, msg.message_id)

    msg = await send_as_chat(
        context,
        chat_id,
        "Меня зовут Надя. И я…\n"
        "\n"
        "Вышла замуж сразу за троих. Да, за троих, всё верно.\n"
        "Но они все три мужа недостаточны… понимаешь?\n"
        "Они — не он… они не Максим.",
        pause=1.2,
    )
    track_msg(context, msg.message_id)

    if gender == "male":
        msg = await send_as_chat(
            context,
            chat_id,
            "Возможно, тебе сложно меня понять.\n"
            "Давай расскажу тебе поподробнее про мою ситуацию "
            "с женской точки зрения.",
            pause=1.0,
        )
        track_msg(context, msg.message_id)

    msg = await send_as_chat(
        context,
        chat_id,
        "Муж №1 — Лёша.\n"
        "Да, он такой же заботливый, но душит меня этим.",
        pause=1.0,
    )
    track_msg(context, msg.message_id)
    msg = await send_as_chat(
        context,
        chat_id,
        "Муж №2 — Женя.\n"
        "С ним также легко, как с Максом, но он слишком ветреный.",
        pause=1.0,
    )
    track_msg(context, msg.message_id)
    msg = await send_as_chat(
        context,
        chat_id,
        "Муж №3 — Костя.\n"
        "Иногда вдохновляет меня, но Макс был больше, "
        "чем просто вдохновение…",
        pause=1.0,
    )
    track_msg(context, msg.message_id)

    if gender == "male":
        question = (
            "Не знаю, правильно ли я поступила, выйдя замуж за них, "
            "если до сих пор не могу отпустить Макса…\n"
            "\n"
            "Дай мужской совет, как мне лучше поступить?"
        )
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Стерпится — слюбится", callback_data="advice_a")],
                [
                    InlineKeyboardButton(
                        "Жалко этих парней!",
                        callback_data="advice_b",
                    )
                ],
            ]
        )
    else:
        question = (
            "Не знаю, правильно ли я поступила, выйдя замуж за них, "
            "если до сих пор не могу отпустить Макса…\n"
            "\n"
            "Вот ты бы что сделала?"
        )
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Поступила бы так же, как ты",
                        callback_data="advice_a",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "Не пудрила мальчикам мозги!",
                        callback_data="advice_b",
                    )
                ],
            ]
        )

    msg = await send_as_chat(
        context,
        chat_id,
        question,
        reply_markup=keyboard,
        pause=1.2,
    )
    track_msg(context, msg.message_id)


async def send_finale(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Поддержать проект", url=PLANETA_URL)],
            [InlineKeyboardButton("Instagram", url=INSTAGRAM_URL)],
            [InlineKeyboardButton("Telegram", url=TELEGRAM_URL)],
            [
                InlineKeyboardButton(
                    "↻ Начать сначала",
                    callback_data="start_story",
                )
            ],
        ]
    )

    msg = await send_as_chat(
        context,
        chat_id,
        "Ой, прости, мне надо бежать…\n"
        "Мужья пришли, не хочу, чтобы они подслушали.\n"
        "\n"
        "Давай ещё раз встретимся. Как насчёт кино?",
        pause=1.0,
    )
    track_msg(context, msg.message_id)
    msg = await send_as_chat(
        context,
        chat_id,
        "Свяжись со мной вот здесь:\n"
        "\n"
        f"Юзер во всех соц.сетях:\n"
        f"@nadyagotmarried\n"
        "\n"
        "Если хочешь меня поддержать — это можно сделать тут:\n"
        f"{PLANETA_URL}\n"
        "\n"
        "Увидимся!",
        reply_markup=keyboard,
        pause=1.2,
    )
    track_msg(context, msg.message_id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    data = load_stats()
    record = ensure_user(data, user)
    is_new = record["starts"] == 0 and record["restarts"] == 0
    save_stats(data)

    first = (user.first_name or "").strip()
    hello = f"{first}…" if first else "Привет…"

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("▶ Начать", callback_data="start_story")]]
    )
    msg = await update.message.reply_text(
        f"{hello}\n"
        "\n"
        "Привет. Давай поговорим начистоту?\n"
        "\n"
        "Если готов(а) — жми.",
        reply_markup=keyboard,
    )
    track_msg(context, msg.message_id)

    if is_new:
        await notify_admin(
            context,
            f"🆕 Новый пользователь\n{format_user(user)}\n\n{summary_text(data)}",
        )


async def start_story(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    chat_id = update.effective_chat.id

    data = load_stats()
    record = ensure_user(data, user)
    is_restart = record["starts"] > 0 or record["restarts"] > 0
    if record["starts"] == 0:
        record["starts"] = 1
        event = "▶ Старт истории"
    else:
        record["restarts"] = int(record.get("restarts") or 0) + 1
        event = "↻ Перезапуск истории"
    save_stats(data)

    if is_restart:
        # Удаляем всю историю чата и начинаем заново
        await clear_chat(context, chat_id)
    else:
        # Первый запуск — просто убираем кнопку со стартового сообщения
        await query.edit_message_reply_markup(reply_markup=None)

    await notify_admin(
        context,
        f"{event}\n{format_user(user)}\n"
        f"Пол: {GENDER_LABELS.get(record.get('gender') or 'unknown')}\n\n"
        f"{summary_text(data)}",
    )

    if not record.get("gender"):
        await ask_gender(context, chat_id)
        return

    await send_story(context, chat_id, get_gender(record, context))


async def set_gender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    gender = query.data.replace("gender_", "")
    user = update.effective_user
    chat_id = update.effective_chat.id

    data = load_stats()
    record = ensure_user(data, user)
    record["gender"] = gender
    context.user_data["gender"] = gender
    save_stats(data)

    await notify_admin(
        context,
        f"👤 Пол: {GENDER_LABELS[gender]}\n{format_user(user)}\n\n{summary_text(data)}",
    )

    # Убираем кнопки с вопроса о поле
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    await send_story(context, chat_id, gender)


async def choose_advice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    choice = query.data.replace("advice_", "")
    user = update.effective_user
    chat_id = update.effective_chat.id

    data = load_stats()
    record = ensure_user(data, user)
    gender = get_gender(record, context)
    record["advice_choice"] = choice
    record["completed"] = int(record.get("completed") or 0) + 1
    save_stats(data)

    await notify_admin(
        context,
        f"💬 Ответ: {ADVICE_LABELS[choice]}\n"
        f"{format_user(user)}\n"
        f"Пол: {GENDER_LABELS.get(gender)}\n\n"
        f"{summary_text(data)}",
    )

    # Убираем кнопки с вариантами ответа
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    msg = await send_as_chat(
        context,
        chat_id,
        advice_reply(gender, choice),
        pause=0.9,
    )
    track_msg(context, msg.message_id)
    await send_finale(context, chat_id)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_CHAT_ID and update.effective_chat.id != ADMIN_CHAT_ID:
        return
    data = load_stats()
    await update.message.reply_text(summary_text(data))


def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit(
            "Нет BOT_TOKEN. Скопируй .env.example в .env и вставь токен от @BotFather."
        )

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CallbackQueryHandler(start_story, pattern="^start_story$"))
    app.add_handler(CallbackQueryHandler(set_gender, pattern="^gender_(male|female)$"))
    app.add_handler(CallbackQueryHandler(choose_advice, pattern="^advice_[ab]$"))

    logger.info("Бот запущен")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
