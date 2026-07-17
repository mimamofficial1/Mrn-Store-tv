# Force Subscribe helper functions

from pyrogram import Client
from pyrogram.errors import UserNotParticipant
from pyrogram.types import InlineKeyboardButton
from plugins.settings_db import (
    get_settings, force_sub_channel_id, force_sub_channel_mode,
    force_sub_channel_link, set_force_sub_link,
)


async def not_joined_channels(client: Client, user_id: int):
    """Return the list of force-sub channel entries this user has NOT joined.
    Returns [] if force_sub is disabled or the user has joined everything."""
    settings = await get_settings()
    if not settings.get("force_sub"):
        return []
    entries = settings.get("force_sub_channels") or []
    missing = []
    for entry in entries:
        ch = force_sub_channel_id(entry)
        try:
            member = await client.get_chat_member(ch, user_id)
            if member.status in ("kicked", "left"):
                missing.append(entry)
        except UserNotParticipant:
            missing.append(entry)
        except Exception:
            # Bot not admin there / invalid channel -> don't lock everyone out
            continue
    return missing


async def force_sub_join_buttons(client: Client, entries):
    """Build one 'Join <channel>' button per row for the given force-sub entries.

    Normal Mode -> regular invite/username link (instant join).
    Join Request Mode -> a link created with creates_join_request=True, so
    tapping it sends a join request instead of joining instantly. The bot
    then auto-approves it (see auto_approve_join_request below)."""
    buttons = []
    for entry in entries:
        ch = force_sub_channel_id(entry)
        mode = force_sub_channel_mode(entry)
        try:
            chat = await client.get_chat(ch)
        except Exception:
            continue

        link = None
        if mode == "request":
            link = force_sub_channel_link(entry)
            if not link:
                # Legacy entry added before join-request links existed -> self-heal
                try:
                    invite = await client.create_chat_invite_link(ch, creates_join_request=True, name="Force Sub (Join Request)")
                    link = invite.invite_link
                    await set_force_sub_link(ch, link)
                except Exception:
                    link = None
        else:
            link = chat.invite_link or (f"https://t.me/{chat.username}" if chat.username else None)

        if link:
            buttons.append([InlineKeyboardButton(f"🔔 Join {chat.title}", url=link)])
    return buttons


@Client.on_chat_join_request()
async def auto_approve_join_request(client: Client, update):
    """For channels added in 'Join Request Mode', auto-approve incoming join
    requests so the force-sub check passes right after the user requests."""
    settings = await get_settings()
    entries = settings.get("force_sub_channels") or []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("mode") == "request" and force_sub_channel_id(entry) == update.chat.id:
            try:
                await client.approve_chat_join_request(update.chat.id, update.from_user.id)
            except Exception:
                pass
            break
