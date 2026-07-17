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
    """Simple health-check endpoint - keeps the hosting platform (Koyeb/
    Heroku/Render) aware the service is alive."""
    return web.json_response(
        {
            "server_status": "running",
            "uptime": _readable_uptime(time.time() - StartTime),
            "telegram_bot": "@" + (StreamBot.username or ""),
            "version": __version__,
        }
    )
