# Force Subscribe helper functions
#
# Join Request Mode design (mirrors a known-working reference implementation):
#   1. Admin adds a channel in "request" mode -> we create a
#      creates_join_request=True invite link and show it as the Join button.
#   2. User taps it -> Telegram fires an on_chat_join_request event -> we
#      record {user_id, channel_id} in the DB. That's the ONLY signal we
#      trust for "request mode" channels - no extra live API calls that can
#      silently misbehave.
#   3. Next /start, we check the DB record first (fast + reliable), then
#      fall back to a direct get_chat_member (covers users who were already
#      an actual channel member before this feature was even turned on).

import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.errors import UserNotParticipant
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from plugins.settings_db import (
    get_settings, force_sub_channel_id, force_sub_channel_mode,
    force_sub_channel_link, set_force_sub_link,
    record_join_request, has_join_request,
)

logger = logging.getLogger(__name__)


@Client.on_chat_join_request()
async def track_join_request(client: Client, update):
    """Record that this user sent a join request to this channel. This is
    the single source of truth for 'request' mode channels - no manual or
    auto approval needed, sending the request is enough."""
    settings = await get_settings()
    entries = settings.get("force_sub_channels") or []
    matched = any(
        isinstance(entry, dict) and entry.get("mode") == "request" and force_sub_channel_id(entry) == update.chat.id
        for entry in entries
    )
    if not matched:
        return

    try:
        await record_join_request(update.from_user.id, update.chat.id)
    except Exception as e:
        logger.error(f"Couldn't save join request for {update.from_user.id} in {update.chat.id}: {e}")
        return

    try:
        await client.send_message(
            update.from_user.id,
            "✅ <b>Request received!</b> Tap /start again to continue.",
        )
    except Exception:
        pass


async def _channel_status(client: Client, entry, user_id: int):
    """Check a single force-sub channel for this user.
    Returns (missing_entry_or_None, button_row_or_None)."""
    ch = force_sub_channel_id(entry)
    mode = force_sub_channel_mode(entry)

    if mode == "request" and await has_join_request(user_id, ch):
        return None, None  # already sent a request - satisfied, no button

    try:
        member = await client.get_chat_member(ch, user_id)
        if member.status not in ("kicked", "banned", "left"):
            if mode == "request":
                # They're already an actual member (e.g. joined before this
                # channel was even set to request mode) - record it so we
                # don't need to hit get_chat_member for them again.
                try:
                    await record_join_request(user_id, ch)
                except Exception:
                    pass
            return None, None
    except UserNotParticipant:
        pass
    except Exception:
        # Bot not admin there / invalid channel -> don't lock everyone out
        return None, None

    # Not satisfied -> build the Join button for this channel
    try:
        chat = await client.get_chat(ch)
    except Exception:
        return entry, None

    link = None
    if mode == "request":
        link = force_sub_channel_link(entry)
        if not link:
            try:
                invite = await client.create_chat_invite_link(ch, creates_join_request=True, name="Force Sub (Join Request)")
                link = invite.invite_link
                await set_force_sub_link(ch, link)
            except Exception as e:
                logger.warning(f"Couldn't create join-request link for {ch}: {e}")
                link = None
    else:
        link = chat.invite_link or (f"https://t.me/{chat.username}" if chat.username else None)

    row = [InlineKeyboardButton(f"🔔 Join {chat.title}", url=link)] if link else None
    return entry, row


async def not_joined_channels(client: Client, user_id: int, settings=None):
    """Return the list of force-sub channel entries this user has NOT joined."""
    missing, _buttons = await get_missing_and_buttons(client, user_id, settings)
    return missing


async def get_missing_and_buttons(client: Client, user_id: int, settings=None):
    """Single pass: returns (missing_entries, join_buttons) together, so we
    never check the same channel twice (once for the missing-list, once for
    the buttons) like the old two-function design used to."""
    if settings is None:
        settings = await get_settings()
    if not settings.get("force_sub"):
        return [], []
    entries = settings.get("force_sub_channels") or []
    results = await asyncio.gather(*[_channel_status(client, entry, user_id) for entry in entries])
    missing = [entry for entry, _row in results if entry is not None]
    buttons = [row for _entry, row in results if row is not None]
    return missing, buttons


async def force_sub_join_buttons(client: Client, entries, user_id: int = None, settings=None):
    """Build one 'Join <channel>' button per row for the given force-sub entries.
    Kept for backward compatibility - prefer get_missing_and_buttons() which
    avoids re-checking each channel a second time."""
    if user_id is None:
        async def _build_only(entry):
            _entry, row = await _channel_status(client, entry, 0)
            return row
        rows = await asyncio.gather(*[_build_only(entry) for entry in entries])
        return [row for row in rows if row is not None]

    rows = await asyncio.gather(*[_channel_status(client, entry, user_id) for entry in entries])
    return [row for _entry, row in rows if row is not None]


@Client.on_callback_query(filters.regex(r"^fsub_verify:"))
async def fsub_verify_callback(client: Client, callback_query):
    """Instant re-check when the user taps 'Try Again' - no need to reopen
    the bot chat, this edits the same message in place with the result
    right away (and shows a popup so it's obvious whether it worked)."""
    user_id = callback_query.from_user.id
    param = callback_query.data.split(":", 1)[1]

    settings = await get_settings()
    missing, buttons = await get_missing_and_buttons(client, user_id, settings)

    if missing:
        await callback_query.answer(
            f"❌ Still {len(missing)} channel(s) pending! Join/request all of them, then try again.",
            show_alert=True,
        )
        buttons = buttons + [[InlineKeyboardButton("🔄 Try Again", callback_data=f"fsub_verify:{param}")]]
        try:
            if callback_query.message.photo:
                await callback_query.message.edit_caption(callback_query.message.caption.html, reply_markup=InlineKeyboardMarkup(buttons))
            else:
                await callback_query.message.edit_text(callback_query.message.text.html, reply_markup=InlineKeyboardMarkup(buttons))
        except Exception:
            pass
        return

    await callback_query.answer("✅ Verified! Tap Continue below.", show_alert=True)
    username = client.me.username
    open_url = f"https://t.me/{username}?start={param}" if param and param != "-" else f"https://t.me/{username}?start=true"
    success_text = "✅ <b>Access granted!</b> Tap the button below to continue."
    try:
        if callback_query.message.photo:
            await callback_query.message.edit_caption(success_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Continue", url=open_url)]]))
        else:
            await callback_query.message.edit_text(success_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Continue", url=open_url)]]))
    except Exception:
        pass
