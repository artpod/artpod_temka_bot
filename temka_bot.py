# =====================
# temka_bot.py
# =====================
import asyncio
import logging
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, ContentType
from aiogram.utils.markdown import hbold, hlink
from dotenv import load_dotenv
from aiogram.client.default import DefaultBotProperties

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
ADMIN_IDS = {int(i) for i in os.getenv("ADMIN_IDS", "").split(",") if i.strip()}

if not BOT_TOKEN or CHANNEL_ID == 0:
    raise SystemExit("Please set BOT_TOKEN and CHANNEL_ID in .env")

# --- Logging ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("temka-bot")


bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))

dp = Dispatcher()

# --- Anti-spam config ---
MAX_MSG_PER_MINUTE = 3          # allow N messages / minute
BAN_THRESHOLD = 6               # if user sends >= this many within a minute -> ban
WINDOW_SECONDS = 60

# State stores
recent_msgs: dict[int, deque[float]] = defaultdict(lambda: deque(maxlen=50))
blocked: set[int] = set()

@dataclass
class ForwardResult:
    ok: bool
    error: str | None = None


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def tg_user_link(user_id: int, username: str | None, full_name: str) -> str:
    """Return a safe clickable identifier for the user."""
    if username:
        return f"@{username} ({full_name})"
    # deep link that opens chat with the user
    return hlink(full_name, f"tg://user?id={user_id}")


def check_rate_limit(user_id: int) -> tuple[bool, bool]:
    """Returns (allowed, now_banned)."""
    now = time.time()
    dq = recent_msgs[user_id]
    # drop old
    while dq and now - dq[0] > WINDOW_SECONDS:
        dq.popleft()
    dq.append(now)
    if user_id in blocked:
        return False, False
    if len(dq) >= BAN_THRESHOLD:
        blocked.add(user_id)
        return False, True
    if len(dq) > MAX_MSG_PER_MINUTE:
        return False, False
    return True, False


async def forward_any_message(msg: Message) -> ForwardResult:
    """Copy message to the target channel with a header showing author info."""
    u = msg.from_user
    if not u:
        return ForwardResult(False, "Unknown sender")

    author = tg_user_link(u.id, u.username, u.full_name)

    header = (
        f"\n{hbold('Надійшла темка')}\n"
        f"Від: {author}\n"
        f"User ID: <code>{u.id}</code>\n"
    )

    try:
        # 1) send a header post (so admin sees author regardless of privacy settings)
        await bot.send_message(CHANNEL_ID, header)

        # 2) copy the original message preserving media/captions
        await msg.copy_to(CHANNEL_ID)

        return ForwardResult(True)
    except Exception as e:  # noqa: BLE001
        logger.exception("Forward failed")
        return ForwardResult(False, str(e))


@dp.message(CommandStart())
async def on_start(msg: Message):
    text = (
        "Привіт! Це бот для анонімного надсилання \"темок\" до приватного каналу.\n\n"
        "Як працює:\n"
        "1) Надішли мені текст, фото, файл або голосове повідомлення — усе буде переслано в канал.\n"
        "2) Після успішної відправки отримаєш: ‘✅ повідомлення переслано’.\n\n"
        f"Обмеження: не більше {MAX_MSG_PER_MINUTE} повідомлень за {WINDOW_SECONDS}с. За спам — блок.\n"
        "Адмін бачить твій @username/ім’я та ID, щоб мати зворотній зв’язок щодо виплат 20%."
    )
    await msg.answer(text)


@dp.message(Command("rules"))
async def rules(msg: Message):
    await msg.answer(
        "Правила: без флуда, шахрайства, скама та забороненого контенту. За спам — миттєвий блок."
    )


@dp.message(Command("stats"))
async def stats(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    await msg.reply(
        f"Blocked users: {len(blocked)}\n"
        f"Tracked users in window: {len(recent_msgs)}"
    )


@dp.message(Command("unblock"))
async def unblock(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.reply("Вкажи user_id: /unblock <id>")
        return
    try:
        uid = int(parts[1])
    except ValueError:
        await msg.reply("user_id має бути числом")
        return
    if uid in blocked:
        blocked.remove(uid)
        await msg.reply(f"Користувача {uid} розблоковано ✅")
    else:
        await msg.reply("Цього користувача не заблоковано")


# Catch-all: any content except commands -> forward
@dp.message(F.content_type.in_({
    ContentType.TEXT,
    ContentType.PHOTO,
    ContentType.DOCUMENT,
    ContentType.VIDEO,
    ContentType.AUDIO,
    ContentType.VOICE,
    ContentType.VIDEO_NOTE,
    ContentType.STICKER,
    ContentType.CONTACT,
    ContentType.LOCATION,
    ContentType.ANIMATION,
}))
async def on_any_message(msg: Message):
    u = msg.from_user
    if not u:
        return

    allowed, now_banned = check_rate_limit(u.id)
    if not allowed:
        if now_banned:
            await msg.answer("🚫 Ви заблоковані за спам. Зверніться до адміна, якщо це помилка.")
        else:
            await msg.answer("⏳ Занадто часто. Спробуйте пізніше.")
        return

    res = await forward_any_message(msg)
    if res.ok:
        await msg.answer("✅ повідомлення переслано")
    else:
        await msg.answer(f"❌ не вдалося переслати: {res.error}")


async def main():
    # Important: ensure the bot is an ADMIN in the target channel with permission to post
    me = await bot.get_me()
    logger.info("Bot started as @%s (id=%s)", me.username, me.id)
    await dp.start_polling(bot, allowed_updates=["message", "edited_message"])


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")

# =====================
# README (Quick start)
# =====================
"""
🔐 What this bot does
- Accepts any user message and forwards (copies) it to your private channel.
- Adds a header with sender identity (username + full name + user_id) so you can contact them for payouts.
- Anti-spam: >3 msgs/min throttle; ≥6 msgs/min => auto-block. Admins can /unblock <id>.

🚀 Setup
1) Create bot via @BotFather → copy token → put into .env as BOT_TOKEN=...
2) Create a private channel (or use an existing one).
3) Add the bot to the channel as ADMIN with permission to Post Messages.
4) Get channel ID:
   • Forward any post from that channel to @getidsbot OR use @userinfobot → copy the chat_id starting with -100...
   • Put it into .env as CHANNEL_ID=-100xxxxxxxxxx
5) (Optional) Put your Telegram numeric user IDs into ADMIN_IDS for /unblock and /stats. Get it via @userinfobot.
6) Install & run:
   python -m venv .venv && . .venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env  # then edit
   python temka_bot.py

🧪 Test it
- DM the bot /start then send a text/photo/file. You should see two posts in the channel: a small header and the copied message.
- The bot replies to you: “✅ повідомлення переслано”.

⚠️ Notes
- If a user hides forwarding info, our header still shows their name/id, so you always see the author.
- Persisting blocklist across restarts is not implemented; if you need it, write blocked user IDs to a JSON file and load on startup.
- If you prefer a single combined post instead of header+copy, you can reconstruct content manually per content type, but copy_to preserves media best.
"""
