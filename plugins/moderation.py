# Ban / Unban / Bot Status commands
# Added for MRN Store TV bot

import time
import psutil
from pyrogram import Client, filters
from pyrogram.types import Message
from config import ADMINS
from plugins.dbusers import db

BOT_START_TIME = time.time()


def get_readable_time(seconds: int) -> str:
    periods = [('day', 86400), ('hour', 3600), ('minute', 60), ('second', 1)]
    result = []
    for name, secs in periods:
        val, seconds = divmod(seconds, secs)
        if val:
            result.append(f"{val} {name}{'s' if val != 1 else ''}")
    return ' '.join(result) if result else '0 seconds'


def _extract_target_id(message: Message):
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id
    if len(message.command) >= 2:
        try:
            return int(message.command[1])
        except ValueError:
            return None
    return None


@Client.on_message(filters.command("ban") & filters.user(ADMINS))
async def ban_user_cmd(client, message: Message):
    target_id = _extract_target_id(message)
    if target_id is None:
        return await message.reply_text(
            "<b>Usage:</b> <code>/ban user_id</code>\nOr reply to that user's message with /ban"
        )
    if target_id in ADMINS:
        return await message.reply_text("<b>❌ You can't ban an admin.</b>")
    if not await db.is_user_exist(target_id):
        return await message.reply_text("<b>This user has never used the bot.</b>")
    await db.ban_user(target_id)
    await message.reply_text(f"<b>✅ User <code>{target_id}</code> has been banned from the bot.</b>")


@Client.on_message(filters.command("unban") & filters.user(ADMINS))
async def unban_user_cmd(client, message: Message):
    target_id = _extract_target_id(message)
    if target_id is None:
        return await message.reply_text(
            "<b>Usage:</b> <code>/unban user_id</code>\nOr reply to that user's message with /unban"
        )
    if not await db.is_user_exist(target_id):
        return await message.reply_text("<b>This user has never used the bot.</b>")
    await db.unban_user(target_id)
    await message.reply_text(f"<b>✅ User <code>{target_id}</code> has been unbanned.</b>")


@Client.on_message(filters.command(["status", "stats"]) & filters.user(ADMINS))
async def bot_status(client, message: Message):
    total_users = await db.total_users_count()
    total_banned = await db.total_banned_count()
    try:
        cpu = psutil.cpu_percent(interval=0.5)
        ram = psutil.virtual_memory().percent
    except Exception:
        cpu = ram = 0
    uptime = get_readable_time(int(time.time() - BOT_START_TIME))
    text = (
        "<b>🤖 BOT STATUS</b>\n\n"
        f"👤 Users - <code>{total_users}</code>\n"
        f"🚫 Ban Users - <code>{total_banned}</code>\n"
        f"⚙️ CPU - <code>{cpu}%</code>\n"
        f"💾 RAM - <code>{ram}%</code>\n"
        f"⚡ Uptime - <code>{uptime}</code>"
    )
    await message.reply_text(text)
