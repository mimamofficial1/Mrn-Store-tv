import re
import os
import json
import time
import base64
import asyncio
import datetime
from pyrogram import Client, filters, StopPropagation
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from config import ADMINS, LOG_CHANNEL, WEBSITE_URL, WEBSITE_URL_MODE
from plugins.admins_db import is_admin
from plugins.settings_db import (
    create_special_link,
    get_special_link,
    update_special_link,
    append_special_link_messages,
    delete_special_link,
)
from plugins.commands import invalidate_special_link_cache

# Per-user in-memory session for the multi-step Create/Modify/Delete flow.
# Only one flow can be active per user at a time.
_sessions = {}

_LINK_ID_RE = re.compile(r"(?:start=|Mrn_Officialx=)BATCH-([A-Za-z0-9_\-]+)")


async def _is_mod(user_id):
    return user_id in ADMINS or await is_admin(user_id)


def _extract_link_id(text):
    if not text:
        return None
    text = text.strip()
    m = _LINK_ID_RE.search(text)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_\-]+", text):
        return text
    return None


def _build_share_link(username, link_id):
    if WEBSITE_URL_MODE:
        return f"{WEBSITE_URL}?Mrn_Officialx=BATCH-{link_id}"
    return f"https://t.me/{username}?start=BATCH-{link_id}"


def _status_text(count):
    return f"<b>Stored Messages:</b> {count}\n\n<i>Want to add another message? Just send it!</i>"


def _status_buttons():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏸ Pause", callback_data="sl_pause")],
        [InlineKeyboardButton("🔗 Generate Link", callback_data="sl_generate")],
        [InlineKeyboardButton("❌ Cancel", callback_data="sl_cancel")],
    ])


async def _persist_new_link(client, owner_id, messages):
    """Uploads a Special.json record marker into LOG_CHANNEL (purely so the
    link_id is derived the exact same way /batch does - keeps both kinds of
    links looking identical/interchangeable) and saves the real, editable
    content in Mongo."""
    fname = f"special_{owner_id}_{int(time.time())}.json"
    with open(fname, "w+") as f:
        json.dump(messages, f)
    post = await client.send_document(LOG_CHANNEL, fname, file_name="Special.json", caption="⚠️ Special Link Generated.")
    os.remove(fname)
    link_id = base64.urlsafe_b64encode(str(post.id).encode("ascii")).decode().strip("=")
    await create_special_link(link_id, owner_id, messages)
    return link_id


async def _send_modify_panel(client, chat_id, record):
    link_id = record["_id"]
    created = record.get("created_on")
    created_str = created.strftime("%Y-%m-%d") if created else "-"
    protect = record.get("protect_content")
    whitelist = record.get("whitelist") or []
    text = (
        "<b>Customize this link using the options below.</b>\n\n"
        f"- message's count: {len(record.get('messages') or [])}\n"
        f"- created on: {created_str}\n"
        f"- protect content: {'ON' if protect else ('OFF' if protect is False else 'default')}\n"
        f"- whitelisted users: {len(whitelist) if whitelist else 'none (open to everyone)'}"
    )
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Edit Contents", callback_data=f"sl_edit:{link_id}"),
         InlineKeyboardButton("👥 Whitelisters", callback_data=f"sl_wl:{link_id}")],
        [InlineKeyboardButton("🔒 Protect Content", callback_data=f"sl_protect:{link_id}"),
         InlineKeyboardButton("⏳ Auto Expire", callback_data=f"sl_expire:{link_id}")],
        [InlineKeyboardButton("🔗 Get Link", callback_data=f"sl_getlink:{link_id}"),
         InlineKeyboardButton("🗑 Delete", callback_data=f"sl_delete:{link_id}")],
    ])
    await client.send_message(chat_id, text, reply_markup=buttons)


# --- Entry point --------------------------------------------------------

@Client.on_message(filters.command(["special_link"]) & filters.private & filters.incoming)
async def special_link_menu(client, message):
    if not message.from_user or not await _is_mod(message.from_user.id):
        return await message.reply("<b>🚫 Moderators only.</b>")
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("CREATE", callback_data="sl_menu:create"),
         InlineKeyboardButton("MODIFY", callback_data="sl_menu:modify")],
        [InlineKeyboardButton("DELETE", callback_data="sl_menu:delete"),
         InlineKeyboardButton("CLOSE", callback_data="sl_menu:close")],
    ])
    await message.reply(
        "<b>Do you want to create a new special link, or modify an existing one, or delete it?</b>",
        reply_markup=buttons,
    )


@Client.on_callback_query(filters.regex(r"^sl_menu:"))
async def special_link_menu_cb(client, query):
    if not await _is_mod(query.from_user.id):
        return await query.answer("Not authorized.", show_alert=True)
    action = query.data.split(":", 1)[1]
    user_id = query.from_user.id

    if action == "close":
        await query.answer()
        return await query.message.delete()

    if action == "create":
        _sessions[user_id] = {"mode": "create", "messages": [], "collecting": True, "lock": asyncio.Lock()}
        msg = await query.message.edit_text(_status_text(0), reply_markup=_status_buttons())
        _sessions[user_id]["status_chat_id"] = msg.chat.id
        _sessions[user_id]["status_msg_id"] = msg.id
        return await query.answer()

    if action == "modify":
        _sessions[user_id] = {"mode": "await_modify_link"}
        await query.message.edit_text("<b>Send Your Special Link For Modify</b>")
        return await query.answer()

    if action == "delete":
        _sessions[user_id] = {"mode": "await_delete_link"}
        await query.message.edit_text("<b>Send the special link you want to delete</b>")
        return await query.answer()


# --- Create / Edit content loop, and text-prompt follow-ups -------------

def _has_active_session(_, __, message):
    return bool(message.from_user) and message.from_user.id in _sessions


@Client.on_message(filters.private & filters.incoming & filters.create(_has_active_session), group=-1)
async def special_link_capture(client, message):
    user_id = message.from_user.id
    session = _sessions.get(user_id)
    if not session:
        return

    text = (message.text or "").strip()
    if text.lower() == "/cancel":
        _sessions.pop(user_id, None)
        await message.reply("Cancelled.")
        raise StopPropagation

    mode = session.get("mode")

    if mode in ("create", "edit"):
        if not session.get("collecting", True):
            return  # paused - let this message fall through to normal handling
        try:
            post = await message.copy(LOG_CHANNEL)
        except Exception as e:
            await message.reply(f"<b>❌ Couldn't store that message:</b> {e}")
            raise StopPropagation
        lock = session.setdefault("lock", asyncio.Lock())
        async with lock:
            session["messages"].append({"channel_id": LOG_CHANNEL, "msg_id": post.id})
            status_text = _status_text(len(session["messages"]))
            try:
                await client.edit_message_text(session["status_chat_id"], session["status_msg_id"], status_text, reply_markup=_status_buttons())
            except Exception:
                m = await message.reply(status_text, reply_markup=_status_buttons())
                session["status_chat_id"] = m.chat.id
                session["status_msg_id"] = m.id
        raise StopPropagation

    if mode == "await_modify_link":
        link_id = _extract_link_id(text)
        record = await get_special_link(link_id) if link_id else None
        _sessions.pop(user_id, None)
        if not record:
            await message.reply("<b>❌ Not a valid special link created by this bot.</b>")
            raise StopPropagation
        await _send_modify_panel(client, message.chat.id, record)
        raise StopPropagation

    if mode == "await_delete_link":
        link_id = _extract_link_id(text)
        record = await get_special_link(link_id) if link_id else None
        _sessions.pop(user_id, None)
        if not record:
            await message.reply("<b>❌ Not a valid special link created by this bot.</b>")
        else:
            await delete_special_link(link_id)
            invalidate_special_link_cache(link_id)
            await message.reply("<b>🗑 Special link deleted.</b>")
        raise StopPropagation

    if mode == "await_whitelist":
        ids = [int(p) for p in re.split(r"[,\s]+", text) if p.strip().isdigit()]
        link_id = session["link_id"]
        _sessions.pop(user_id, None)
        await update_special_link(link_id, {"whitelist": ids})
        invalidate_special_link_cache(link_id)
        record = await get_special_link(link_id)
        await message.reply(
            f"<b>✅ Whitelist cleared - open to everyone again.</b>" if not ids
            else f"<b>✅ Whitelist updated ({len(ids)} user(s)).</b>"
        )
        if record:
            await _send_modify_panel(client, message.chat.id, record)
        raise StopPropagation

    if mode == "await_expire":
        link_id = session["link_id"]
        try:
            days = float(text)
        except ValueError:
            await message.reply("<b>Send a number of days (e.g. 7). Send 0 to disable expiry.</b>")
            raise StopPropagation
        expires_at = None if days <= 0 else datetime.datetime.utcnow() + datetime.timedelta(days=days)
        _sessions.pop(user_id, None)
        await update_special_link(link_id, {"expires_at": expires_at})
        invalidate_special_link_cache(link_id)
        record = await get_special_link(link_id)
        await message.reply("<b>✅ Auto-expire disabled.</b>" if not expires_at else f"<b>✅ This link will expire in {days:g} day(s).</b>")
        if record:
            await _send_modify_panel(client, message.chat.id, record)
        raise StopPropagation


# --- Pause / Generate / Cancel (Create & Edit) ---------------------------

@Client.on_callback_query(filters.regex(r"^sl_(pause|generate|cancel)$"))
async def special_link_action_cb(client, query):
    user_id = query.from_user.id
    if not await _is_mod(user_id):
        return await query.answer("Not authorized.", show_alert=True)
    session = _sessions.get(user_id)
    if not session or session.get("mode") not in ("create", "edit"):
        return await query.answer("No active session. Send /special_link to start.", show_alert=True)
    action = query.data.split("_", 1)[1]

    if action == "pause":
        session["collecting"] = False
        return await query.answer("Paused. Tap Generate Link when you're ready, or Cancel.")

    if action == "cancel":
        _sessions.pop(user_id, None)
        await query.message.edit_text("<b>❌ Cancelled.</b>")
        return await query.answer()

    if action == "generate":
        messages = session["messages"]
        if session["mode"] == "create" and not messages:
            return await query.answer("Store at least one message first.", show_alert=True)

        username = (await client.get_me()).username
        if session["mode"] == "create":
            link_id = await _persist_new_link(client, user_id, messages)
        else:
            link_id = session["link_id"]
            if messages:
                await append_special_link_messages(link_id, messages)
        invalidate_special_link_cache(link_id)
        share_link = _build_share_link(username, link_id)
        _sessions.pop(user_id, None)

        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Modify Link", callback_data=f"sl_openmodify:{link_id}")],
            [InlineKeyboardButton("↗️ Share URL", url=f"https://t.me/share/url?url={share_link}")],
        ])
        await query.message.edit_text(
            f"<b>⭕ Here is your special link:</b>\n\n🔗 {share_link}",
            reply_markup=buttons,
            disable_web_page_preview=True,
        )
        return await query.answer("Link generated!")


# --- Modify panel actions -------------------------------------------------

@Client.on_callback_query(filters.regex(r"^sl_openmodify:"))
async def sl_openmodify_cb(client, query):
    if not await _is_mod(query.from_user.id):
        return await query.answer("Not authorized.", show_alert=True)
    link_id = query.data.split(":", 1)[1]
    record = await get_special_link(link_id)
    if not record:
        return await query.answer("Link not found.", show_alert=True)
    await query.answer()
    await _send_modify_panel(client, query.message.chat.id, record)


@Client.on_callback_query(filters.regex(r"^sl_edit:"))
async def sl_edit_cb(client, query):
    if not await _is_mod(query.from_user.id):
        return await query.answer("Not authorized.", show_alert=True)
    link_id = query.data.split(":", 1)[1]
    record = await get_special_link(link_id)
    if not record:
        return await query.answer("Link not found.", show_alert=True)
    user_id = query.from_user.id
    _sessions[user_id] = {"mode": "edit", "link_id": link_id, "messages": [], "collecting": True, "lock": asyncio.Lock()}
    msg = await query.message.edit_text(
        "<b>Send the messages you want to add to this link.</b>\n\n" + _status_text(len(record.get("messages") or [])),
        reply_markup=_status_buttons(),
    )
    _sessions[user_id]["status_chat_id"] = msg.chat.id
    _sessions[user_id]["status_msg_id"] = msg.id
    await query.answer()


@Client.on_callback_query(filters.regex(r"^sl_wl:"))
async def sl_wl_cb(client, query):
    if not await _is_mod(query.from_user.id):
        return await query.answer("Not authorized.", show_alert=True)
    link_id = query.data.split(":", 1)[1]
    _sessions[query.from_user.id] = {"mode": "await_whitelist", "link_id": link_id}
    await query.message.edit_text(
        "<b>Send the user IDs to whitelist for this link, separated by spaces or commas.</b>\n"
        "Only these users will be able to open it. Send <code>0</code> to clear the whitelist."
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^sl_protect:"))
async def sl_protect_cb(client, query):
    if not await _is_mod(query.from_user.id):
        return await query.answer("Not authorized.", show_alert=True)
    link_id = query.data.split(":", 1)[1]
    record = await get_special_link(link_id)
    if not record:
        return await query.answer("Link not found.", show_alert=True)
    new_val = not record.get("protect_content", False)
    await update_special_link(link_id, {"protect_content": new_val})
    invalidate_special_link_cache(link_id)
    record["protect_content"] = new_val
    await query.answer(f"Protect content: {'ON' if new_val else 'OFF'}")
    try:
        await query.message.delete()
    except Exception:
        pass
    await _send_modify_panel(client, query.message.chat.id, record)


@Client.on_callback_query(filters.regex(r"^sl_expire:"))
async def sl_expire_cb(client, query):
    if not await _is_mod(query.from_user.id):
        return await query.answer("Not authorized.", show_alert=True)
    link_id = query.data.split(":", 1)[1]
    _sessions[query.from_user.id] = {"mode": "await_expire", "link_id": link_id}
    await query.message.edit_text(
        "<b>Send the number of days after which this link should expire.</b>\nSend <code>0</code> to disable expiry."
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^sl_getlink:"))
async def sl_getlink_cb(client, query):
    if not await _is_mod(query.from_user.id):
        return await query.answer("Not authorized.", show_alert=True)
    link_id = query.data.split(":", 1)[1]
    record = await get_special_link(link_id)
    if not record:
        return await query.answer("Link not found.", show_alert=True)
    username = (await client.get_me()).username
    share_link = _build_share_link(username, link_id)
    await query.answer()
    await client.send_message(query.message.chat.id, f"<b>🔗 Link:</b>\n{share_link}", disable_web_page_preview=True)


@Client.on_callback_query(filters.regex(r"^sl_delete:"))
async def sl_delete_cb(client, query):
    if not await _is_mod(query.from_user.id):
        return await query.answer("Not authorized.", show_alert=True)
    link_id = query.data.split(":", 1)[1]
    await delete_special_link(link_id)
    invalidate_special_link_cache(link_id)
    await query.answer("Deleted.", show_alert=True)
    try:
        await query.message.edit_text("<b>🗑 Special link deleted.</b>")
    except Exception:
        pass


# --- Auto-detect: pasting a special link offers to modify it ------------

@Client.on_message(filters.private & filters.incoming & filters.text, group=-1)
async def special_link_autodetect(client, message):
    user_id = message.from_user.id if message.from_user else None
    if not user_id or user_id in _sessions:
        return
    text = message.text.strip()
    if not text or text.startswith("/"):
        return
    if "BATCH-" not in text and "t.me/" not in text.lower() and "telegram.me/" not in text.lower():
        return  # cheap filter first - avoid a DB call on ordinary chat messages
    if not await _is_mod(user_id):
        return
    link_id = _extract_link_id(text)
    if not link_id:
        return
    record = await get_special_link(link_id)
    if not record:
        return
    await message.reply(
        "<b>Do you want to modify this special link?</b>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✏️ Modify Link", callback_data=f"sl_openmodify:{link_id}")]]),
    )
    raise StopPropagation
