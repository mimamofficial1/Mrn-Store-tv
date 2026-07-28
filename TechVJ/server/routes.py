import time
from aiohttp import web
from TechVJ.bot import StreamBot
from TechVJ import StartTime, __version__

routes = web.RouteTableDef()


def _readable_uptime(seconds: int) -> str:
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


@routes.get("/", allow_head=True)
async def root_route_handler(_):
    """Health-check endpoint - keeps the hosting platform (Railway/Koyeb/
    Heroku/Render) aware the service is alive.

    Importantly this also reports whether the Telegram (Pyrogram) client
    is actually connected. Previously this always said "running" even
    when the bot itself had silently disconnected/hung (e.g. blocked on a
    slow MongoDB call) - the web server was fine, only the bot was dead,
    and the host had no way to know something was wrong. Returning a
    non-200 status when the bot is down lets Railway's healthcheck /
    restart policy actually catch and fix it automatically.
    """
    bot_connected = bool(getattr(StreamBot, "is_connected", False))
    payload = {
        "server_status": "running",
        "uptime": _readable_uptime(time.time() - StartTime),
        "telegram_bot": "@" + (StreamBot.username or ""),
        "telegram_bot_connected": bot_connected,
        "version": __version__,
    }
    return web.json_response(payload, status=200 if bot_connected else 503)
