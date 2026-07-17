# /settings admin panel — lets admins change bot behaviour from chat,
# without redeploying (values are stored in MongoDB via settings_db.py)

from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from config import ADMINS, CUSTOM_FILE_CAPTION, PUBLIC_FILE_STORE
from Script import script
from plugins.settings_db import (
    get_settings, update_setting, add_force_sub_channel, remove_force_sub_channel,
    touch_last_used, readable_ago, add_custom_button, remove_custom_button, clear_custom_buttons,
    force_sub_channel_id, force_sub_channel_mode, force_sub_channel_link, set_force_sub_link
)
from plugins.admins_db import dynamic_admin_filter, is_admin, get_all_admins, add_admin, remove_admin, set_permission, PERMISSIONS


def main_menu_text(settings):
    last_used = readable_ago(settings.get("last_used"))
    return (
        "🍿 <b>You can customise more features in your MRN bot from here.</b>\n\n"
        f"⏰ <b>Last Used</b> - {last_used} ago"
    )


def main_menu_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Start Message", callback_data="adm_start")],
        [InlineKeyboardButton("🔘 Custom Button", callback_data="adm_button")],
        [InlineKeyboardButton("♻️ Auto Delete", callback_data="adm_autodel")],
        [InlineKeyboardButton("👥 Admins", callback_data="adm_admins")],
        [InlineKeyboardButton("📊 Bot Status", callback_data="adm_status")],
        [InlineKeyboardButton("🛍 Bot Mode", callback_data="adm_mode")],
        [InlineKeyboardButton("⏱ Restart Bot", callback_data="adm_restart")],
        [InlineKeyboardButton("🔒 Protect Content", callback_data="adm_protect")],
        [InlineKeyboardButton("🍿 Custom Caption", callback_data="adm_caption")],
        [InlineKeyboardButton("📢 Custom Force Subscribe", callback_data="adm_fsub")],
        [InlineKeyboardButton("✖ Close", callback_data="adm_close")],
    ])


@Client.on_message(filters.command(["settings", "customize"]) & dynamic_admin_filter("can_settings"))
async def settings_cmd(client, message: Message):
    settings = await get_settings()
    await touch_last_used()
    await message.reply_text(main_menu_text(settings), reply_markup=main_menu_markup())


@Client.on_callback_query(filters.regex(r"^adm_"))
async def settings_cb(client: Client, query: CallbackQuery):
    try:
        await _settings_cb_inner(client, query)
    finally:
        try:
            await query.answer()
        except Exception:
            pass


async def _settings_cb_inner(client: Client, query: CallbackQuery):
    user = query.from_user
    if not (user.id in ADMINS or await is_admin(user.id)):
        return await query.answer("Admins only!", show_alert=True)

    data = query.data
    settings = await get_settings()

    # ---------------- Main menu ----------------
    if data == "adm_menu":
        await touch_last_used()
        await query.message.edit_text(
            main_menu_text(settings),
            reply_markup=main_menu_markup()
        )

    elif data == "adm_close":
        await query.message.delete()

    elif data == "adm_status":
        import time, psutil, asyncio
        from plugins.dbusers import db
        from plugins.moderation import BOT_START_TIME, get_readable_time
        total_users = await db.total_users_count()
        total_banned = await db.total_banned_count()
        try:
            cpu = await asyncio.to_thread(psutil.cpu_percent, 0.5)
            ram = psutil.virtual_memory().percent
        except Exception:
            cpu = ram = 0
        uptime = get_readable_time(int(time.time() - BOT_START_TIME))
        text = (
            "<b>🤖 BOT STATUS\n\n"
            f"👤 Users - <code>{total_users}</code>\n"
            f"🚫 Ban Users - <code>{total_banned}</code>\n"
            f"⚙️ CPU - <code>{cpu}%</code>\n"
            f"💾 RAM - <code>{ram}%</code>\n"
            f"⚡ Uptime - <code>{uptime}</code></b>"
        )
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("« Back", callback_data="adm_menu")]]
        ))

    # ---------------- Bot Mode ----------------
    elif data == "adm_mode":
        await render_mode_menu(query, settings)

    elif data in ("adm_mode_public", "adm_mode_private"):
        await update_setting("public_mode", data == "adm_mode_public")
        settings["public_mode"] = data == "adm_mode_public"
        await render_mode_menu(query, settings)

    # ---------------- Restart Bot ----------------
    elif data == "adm_restart":
        await query.message.edit_text("<b>♻️ Restarting bot, please wait...</b>")
        import os, sys
        os.execv(sys.executable, [sys.executable] + sys.argv)

    # ---------------- Admins ----------------
    elif data == "adm_admins":
        await render_admins_menu(query)

    elif data == "adm_admins_add":
        await query.message.reply_text(
            "<b>Send the user ID to make admin, or forward a message from them.</b>\n/cancel to cancel."
        )
        ans = await client.ask(query.message.chat.id, "")
        if ans.text and ans.text.strip() == "/cancel":
            await ans.reply_text("Cancelled.")
        else:
            target_id = None
            if ans.forward_from:
                target_id = ans.forward_from.id
            elif ans.text:
                try:
                    target_id = int(ans.text.strip())
                except ValueError:
                    pass
            if not target_id:
                await ans.reply_text("<b>❌ Invalid input.</b>")
            elif target_id in ADMINS:
                await ans.reply_text("<b>This user is already an owner-level admin.</b>")
            else:
                await add_admin(target_id)
                await ans.reply_text(f"<b>✅ <code>{target_id}</code> added as admin.</b>")
        await render_admins_menu(query, edit=False)

    elif data == "adm_admins_list":
        admins = await get_all_admins()
        if not admins:
            return await query.answer("No admins added yet (besides the owner).", show_alert=True)
        buttons = []
        for adm in admins:
            buttons.append([InlineKeyboardButton(f"👤 {adm['_id']}", callback_data=f"adm_admin_view_{adm['_id']}")])
        buttons.append([InlineKeyboardButton("« Back", callback_data="adm_admins")])
        await query.message.edit_text("<b>Tap an admin to manage:</b>", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("adm_admin_view_"):
        target_id = int(data.replace("adm_admin_view_", "", 1))
        await render_admin_detail(query, target_id)

    elif data.startswith("adm_admin_toggle_"):
        # data format: adm_admin_toggle_<perm>_<id>
        parts = data.split("_")
        target_id = int(parts[-1])
        perm = "_".join(parts[3:-1])
        from plugins.admins_db import get_admin
        adm = await get_admin(target_id)
        current = bool(adm.get(perm, False)) if adm else False
        await set_permission(target_id, perm, not current)
        await render_admin_detail(query, target_id)

    elif data.startswith("adm_admin_remove_"):
        target_id = int(data.replace("adm_admin_remove_", "", 1))
        await remove_admin(target_id)
        await query.answer("Admin removed.", show_alert=True)
        await render_admins_menu(query)

    # ---------------- Custom Button ----------------
    elif data == "adm_button":
        await render_button_menu(query, settings)

    elif data == "adm_button_add":
        await query.message.reply_text("<b>Send the button text.</b>\n/cancel to cancel.")
        ans1 = await client.ask(query.message.chat.id, "")
        if ans1.text and ans1.text.strip() != "/cancel":
            btn_text = ans1.text.strip()
            await ans1.reply_text("<b>Now send the button URL.</b>\n/cancel to cancel.")
            ans2 = await client.ask(query.message.chat.id, "")
            if ans2.text and ans2.text.strip() != "/cancel" and ans2.text.strip().startswith(("http://", "https://")):
                await add_custom_button(btn_text, ans2.text.strip())
                await ans2.reply_text("<b>✅ Button added.</b>")
            elif ans2.text:
                await ans2.reply_text("<b>❌ That doesn't look like a valid URL (must start with http:// or https://).</b>")
        settings = await get_settings()
        await render_button_menu(query, settings, edit=False)

    elif data == "adm_button_remove":
        settings = await get_settings()
        flat = [b for row in (settings.get("custom_buttons") or []) for b in row]
        if not flat:
            return await query.answer("No custom buttons yet.", show_alert=True)
        buttons = [[InlineKeyboardButton(f"❌ {b['text']}", callback_data=f"adm_button_rm_{i}")] for i, b in enumerate(flat)]
        buttons.append([InlineKeyboardButton("« Back", callback_data="adm_button")])
        await query.message.edit_text("<b>Tap a button to remove it:</b>", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("adm_button_rm_"):
        index = int(data.replace("adm_button_rm_", "", 1))
        await remove_custom_button(index)
        settings = await get_settings()
        await render_button_menu(query, settings)

    elif data == "adm_button_clear":
        await clear_custom_buttons()
        await query.answer("All custom buttons cleared.", show_alert=True)
        settings = await get_settings()
        await render_button_menu(query, settings)

    # ---------------- Force Subscribe ----------------
    elif data == "adm_fsub":
        await render_fsub_menu(query, settings)

    elif data == "adm_fsub_toggle":
        new_state = not settings.get("force_sub", False)
        await update_setting("force_sub", new_state)
        settings["force_sub"] = new_state
        await render_fsub_menu(query, settings)

    elif data == "adm_fsub_add":
        prompt = await query.message.reply_text(
            "<b>Send the channel username (e.g. @Mrn_Officialx) or channel ID.</b>\n\n"
            "Make sure the bot is an <b>admin</b> in that channel.\n/cancel to cancel."
        )
        ans = await client.ask(query.message.chat.id, "")
        if ans.text and ans.text.strip() == "/cancel":
            await ans.reply_text("Cancelled.")
            settings = await get_settings()
            await render_fsub_menu(query, settings, edit=False)
        elif ans.text:
            channel = ans.text.strip()
            try:
                chat = await client.get_chat(channel)
                member = await client.get_chat_member(chat.id, "me")
                if not member.privileges:
                    await ans.reply_text("<b>⚠️ I must be an admin in that channel first.</b>")
                    settings = await get_settings()
                    await render_fsub_menu(query, settings, edit=False)
                else:
                    await ans.reply_text(
                        f"<i>Choose Force Sub Mode</i>",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("Normal Mode", callback_data=f"adm_fsub_mode_normal_{chat.id}")],
                            [InlineKeyboardButton("Join Request Mode", callback_data=f"adm_fsub_mode_request_{chat.id}")],
                        ])
                    )
            except Exception as e:
                await ans.reply_text(f"<b>❌ Couldn't verify that channel.</b>\n<code>{e}</code>")
                settings = await get_settings()
                await render_fsub_menu(query, settings, edit=False)

    elif data.startswith("adm_fsub_mode_"):
        # data format: adm_fsub_mode_<normal|request>_<chat_id>
        parts = data.split("_")
        mode = parts[3]
        chat_id = int(parts[4])
        try:
            chat = await client.get_chat(chat_id)
            title = chat.title
        except Exception:
            title = str(chat_id)

        link = None
        if mode == "request":
            # Join Request Mode needs a link that actually creates a join
            # request (not a normal instant-join link), so the bot can
            # auto-approve it. A plain chat.invite_link would just add the
            # user instantly and never trigger the approval flow.
            try:
                invite = await client.create_chat_invite_link(chat_id, creates_join_request=True, name="Force Sub (Join Request)")
                link = invite.invite_link
            except Exception as e:
                await query.message.edit_text(
                    f"<b>❌ Couldn't create a join-request link for {title}.</b>\n<code>{e}</code>\n\n"
                    "Make sure I'm an admin there with the <b>Invite Users via Link</b> permission.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❮ BACK", callback_data="adm_fsub")]])
                )
                return

        await add_force_sub_channel(chat_id, mode, link)
        mode_label = "Join Request Mode" if mode == "request" else "Normal Mode"
        await query.message.edit_text(
            f"✨ <i>Successfully Added {title} As Your Force Sub Channel</i>\n<b>Mode:</b> {mode_label}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❮ BACK", callback_data="adm_fsub")]])
        )

    elif data == "adm_fsub_remove":
        channels = settings.get("force_sub_channels") or []
        if not channels:
            return await query.answer("No channels added yet.", show_alert=True)
        buttons = []
        for entry in channels:
            ch = force_sub_channel_id(entry)
            mode = force_sub_channel_mode(entry)
            try:
                chat = await client.get_chat(ch)
                label = chat.title
            except Exception:
                label = str(ch)
            tag = " (Request)" if mode == "request" else ""
            buttons.append([InlineKeyboardButton(f"❌ {label}{tag}", callback_data=f"adm_fsub_rm_{ch}")])
        buttons.append([InlineKeyboardButton("« Back", callback_data="adm_fsub")])
        await query.message.edit_text("<b>Tap a channel to remove it:</b>", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("adm_fsub_rm_"):
        ch = data.replace("adm_fsub_rm_", "", 1)
        try:
            ch = int(ch)
        except ValueError:
            pass
        await remove_force_sub_channel(ch)
        settings = await get_settings()
        await render_fsub_menu(query, settings)

    elif data == "adm_fsub_msg":
        await query.message.reply_text("<b>Send the new Force Subscribe message text.</b>\n/cancel to cancel.")
        ans = await client.ask(query.message.chat.id, "")
        if ans.text and ans.text.strip() != "/cancel":
            await update_setting("force_sub_message", ans.text)
            await ans.reply_text("<b>✅ Force Subscribe message updated.</b>")
        settings = await get_settings()
        await render_fsub_menu(query, settings, edit=False)

    # ---------------- Protect Content ----------------
    elif data == "adm_protect":
        await render_protect_menu(query, settings)

    elif data in ("adm_protect_on", "adm_protect_off"):
        await update_setting("protect_content", data == "adm_protect_on")
        settings["protect_content"] = data == "adm_protect_on"
        await render_protect_menu(query, settings)

    # ---------------- Auto Delete ----------------
    elif data == "adm_autodel":
        await render_autodel_menu(query, settings)

    elif data == "adm_autodel_toggle":
        new_state = not settings.get("auto_delete", True)
        await update_setting("auto_delete", new_state)
        settings["auto_delete"] = new_state
        await render_autodel_menu(query, settings)

    elif data == "adm_autodel_time":
        await query.message.reply_text("<b>Send auto-delete time in minutes (e.g. 30).</b>\n/cancel to cancel.")
        ans = await client.ask(query.message.chat.id, "")
        if ans.text and ans.text.strip() != "/cancel":
            try:
                minutes = int(ans.text.strip())
                await update_setting("auto_delete_time", minutes * 60)
                await ans.reply_text(f"<b>✅ Auto delete time set to {minutes} minutes.</b>")
            except ValueError:
                await ans.reply_text("<b>❌ Please send a number.</b>")
        settings = await get_settings()
        await render_autodel_menu(query, settings, edit=False)

    # ---------------- Custom Caption ----------------
    elif data == "adm_caption":
        await render_caption_menu(query, settings)

    elif data == "adm_caption_edit":
        await query.message.reply_text(
            "<b>Send the new caption format.</b>\n\n"
            "Variables: <code>{file_name}</code> <code>{file_size}</code> <code>{file_caption}</code>\n/cancel to cancel."
        )
        ans = await client.ask(query.message.chat.id, "")
        if ans.text and ans.text.strip() != "/cancel":
            await update_setting("custom_caption", ans.text)
            await ans.reply_text("<b>✅ Custom caption updated.</b>")
        settings = await get_settings()
        await render_caption_menu(query, settings, edit=False)

    elif data == "adm_caption_show":
        current = settings.get("custom_caption") or CUSTOM_FILE_CAPTION
        await query.message.reply_text(f"<b>Current caption format:</b>\n\n<code>{current}</code>")
        await query.answer()

    elif data == "adm_caption_delete":
        await update_setting("custom_caption", None)
        settings["custom_caption"] = None
        await query.answer("Reset to default caption.", show_alert=True)
        await render_caption_menu(query, settings)

    # ---------------- Start Message ----------------
    elif data == "adm_start":
        await render_start_menu(query, settings)

    elif data == "adm_start_edit":
        await query.message.reply_text(
            "<b>Send the new start message text.</b>\n\n"
            "You can use <code>{}</code> <code>{}</code> as placeholders for user mention and bot mention (optional).\n/cancel to cancel."
        )
        ans = await client.ask(query.message.chat.id, "")
        if ans.text and ans.text.strip() != "/cancel":
            await update_setting("start_message", ans.text)
            await ans.reply_text("<b>✅ Start message updated.</b>")
        settings = await get_settings()
        await render_start_menu(query, settings, edit=False)

    elif data == "adm_start_reset":
        await update_setting("start_message", None)
        settings["start_message"] = None
        await query.answer("Reset to default start message.", show_alert=True)
        await render_start_menu(query, settings)


async def render_fsub_menu(query, settings, edit=True):
    state = "✅ ON" if settings.get("force_sub") else "❌ OFF"
    channels = settings.get("force_sub_channels") or []
    text = (
        "<b>📢 FORCE SUBSCRIBE</b>\n\n"
        "Users must join the added channel(s) before using the bot.\n\n"
        f"<b>Status:</b> {state}\n"
        f"<b>Channels added:</b> {len(channels)}"
    )
    buttons = [
        [InlineKeyboardButton("➕ Add Channel", callback_data="adm_fsub_add"),
         InlineKeyboardButton("➖ Remove Channel", callback_data="adm_fsub_remove")],
        [InlineKeyboardButton("Toggle ON/OFF", callback_data="adm_fsub_toggle")],
        [InlineKeyboardButton("✏️ Edit Message", callback_data="adm_fsub_msg")],
        [InlineKeyboardButton("« Back", callback_data="adm_menu")],
    ]
    markup = InlineKeyboardMarkup(buttons)
    if edit:
        await query.message.edit_text(text, reply_markup=markup)
    else:
        await query.message.reply_text(text, reply_markup=markup)


async def render_protect_menu(query, settings, edit=True):
    state = "✅ ENABLED" if settings.get("protect_content") else "❌ DISABLED"
    text = (
        "<b>🔒 PROTECT CONTENT</b>\n\n"
        "Restrict users from forwarding/saving files sent by this bot.\n\n"
        f"<b>Status:</b> {state}"
    )
    buttons = [
        [InlineKeyboardButton("✅ Enable", callback_data="adm_protect_on"),
         InlineKeyboardButton("❌ Disable", callback_data="adm_protect_off")],
        [InlineKeyboardButton("« Back", callback_data="adm_menu")],
    ]
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def render_autodel_menu(query, settings, edit=True):
    state = "✅ ON" if settings.get("auto_delete") else "❌ OFF"
    minutes = max(1, settings.get("auto_delete_time", 1800) // 60)
    text = (
        "<b>♻️ AUTO DELETE</b>\n\n"
        "Automatically deletes delivered files after a set time.\n\n"
        f"<b>Status:</b> {state}\n"
        f"<b>Delete Time:</b> {minutes} minutes"
    )
    buttons = [
        [InlineKeyboardButton("Toggle ON/OFF", callback_data="adm_autodel_toggle")],
        [InlineKeyboardButton("⏱ Set Time", callback_data="adm_autodel_time")],
        [InlineKeyboardButton("« Back", callback_data="adm_menu")],
    ]
    markup = InlineKeyboardMarkup(buttons)
    if edit:
        await query.message.edit_text(text, reply_markup=markup)
    else:
        await query.message.reply_text(text, reply_markup=markup)


async def render_caption_menu(query, settings, edit=True):
    text = "<b>🎬 CUSTOM CAPTION</b>\n\nSet the caption format used when files are delivered."
    buttons = [
        [InlineKeyboardButton("✏️ Edit Caption", callback_data="adm_caption_edit")],
        [InlineKeyboardButton("👁 Show Caption", callback_data="adm_caption_show"),
         InlineKeyboardButton("🗑 Reset", callback_data="adm_caption_delete")],
        [InlineKeyboardButton("« Back", callback_data="adm_menu")],
    ]
    markup = InlineKeyboardMarkup(buttons)
    if edit:
        await query.message.edit_text(text, reply_markup=markup)
    else:
        await query.message.reply_text(text, reply_markup=markup)


async def render_start_menu(query, settings, edit=True):
    text = "<b>📝 START MESSAGE</b>\n\nThis text is shown when a user sends /start."
    buttons = [
        [InlineKeyboardButton("✏️ Edit Start Text", callback_data="adm_start_edit")],
        [InlineKeyboardButton("🗑 Reset to Default", callback_data="adm_start_reset")],
        [InlineKeyboardButton("« Back", callback_data="adm_menu")],
    ]
    markup = InlineKeyboardMarkup(buttons)
    if edit:
        await query.message.edit_text(text, reply_markup=markup)
    else:
        await query.message.reply_text(text, reply_markup=markup)


async def render_mode_menu(query, settings, edit=True):
    public_mode = settings.get("public_mode")
    if public_mode is None:
        public_mode = PUBLIC_FILE_STORE
    state = "🛍 Public Mode" if public_mode else "🔒 Private Mode"
    text = (
        "<b>🛍 BOT MODE</b>\n\n"
        "- <b>Public Mode:</b> any user can generate share links by sending files.\n"
        "- <b>Private Mode:</b> only admins can generate share links.\n\n"
        f"<b>Current Mode:</b> {state}"
    )
    buttons = [
        [InlineKeyboardButton("🛍 Public", callback_data="adm_mode_public"),
         InlineKeyboardButton("🔒 Private", callback_data="adm_mode_private")],
        [InlineKeyboardButton("« Back", callback_data="adm_menu")],
    ]
    markup = InlineKeyboardMarkup(buttons)
    if edit:
        await query.message.edit_text(text, reply_markup=markup)
    else:
        await query.message.reply_text(text, reply_markup=markup)


async def render_admins_menu(query, edit=True):
    admins = await get_all_admins()
    text = (
        "<b>👥 ADMINS</b>\n\n"
        "Add extra admins and control what each one can do.\n"
        f"<b>Extra admins added:</b> {len(admins)}"
    )
    buttons = [
        [InlineKeyboardButton("➕ Add Admin", callback_data="adm_admins_add")],
        [InlineKeyboardButton("📋 Manage Admins", callback_data="adm_admins_list")],
        [InlineKeyboardButton("« Back", callback_data="adm_menu")],
    ]
    markup = InlineKeyboardMarkup(buttons)
    if edit:
        await query.message.edit_text(text, reply_markup=markup)
    else:
        await query.message.reply_text(text, reply_markup=markup)


async def render_admin_detail(query, target_id):
    from plugins.admins_db import get_admin
    adm = await get_admin(target_id) or {}
    text = f"<b>👤 Admin: <code>{target_id}</code></b>\n\nTap a permission to toggle it:"
    buttons = []
    for perm, label in PERMISSIONS.items():
        state = "✅" if adm.get(perm) else "❌"
        buttons.append([InlineKeyboardButton(f"{state} {label}", callback_data=f"adm_admin_toggle_{perm}_{target_id}")])
    buttons.append([InlineKeyboardButton("🗑 Remove Admin", callback_data=f"adm_admin_remove_{target_id}")])
    buttons.append([InlineKeyboardButton("« Back", callback_data="adm_admins_list")])
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def render_button_menu(query, settings, edit=True):
    rows = settings.get("custom_buttons") or []
    count = sum(len(r) for r in rows)
    text = (
        "<b>🔘 CUSTOM BUTTON</b>\n\n"
        "Add custom URL buttons that get attached to every file the bot delivers.\n"
        "Up to 2 buttons per row, multiple rows supported.\n\n"
        f"<b>Buttons added:</b> {count}"
    )
    buttons = [
        [InlineKeyboardButton("➕ Add Button", callback_data="adm_button_add"),
         InlineKeyboardButton("➖ Remove Button", callback_data="adm_button_remove")],
        [InlineKeyboardButton("🗑 Clear All", callback_data="adm_button_clear")],
        [InlineKeyboardButton("« Back", callback_data="adm_menu")],
    ]
    markup = InlineKeyboardMarkup(buttons)
    if edit:
        await query.message.edit_text(text, reply_markup=markup)
    else:
        await query.message.reply_text(text, reply_markup=markup)
