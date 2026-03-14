"""
HTTP сервер для keep-alive на Render + healthcheck endpoint.
"""
import json
import logging
import os

from aiohttp import web, ClientSession, ClientTimeout

log = logging.getLogger(__name__)

# Время последней публикации — обновляется из scheduler
last_post_time: str | None = None


async def handle_ping(request):
    return web.Response(text="OK")


async def handle_health(request):
    db_ok = False
    try:
        from database import get_pool
        pool = await get_pool()
        await pool.fetchval("SELECT 1")
        db_ok = True
    except Exception:
        pass

    payload = {
        "status": "ok" if db_ok else "degraded",
        "db": "ok" if db_ok else "error",
        "last_post": last_post_time,
    }
    return web.Response(
        text=json.dumps(payload),
        content_type="application/json",
        status=200 if db_ok else 503,
    )


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/health", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info(f"HTTP сервер запущен на порту {port}")


async def self_ping():
    render_url = os.getenv("RENDER_EXTERNAL_URL")
    if not render_url:
        return
    try:
        async with ClientSession() as s:
            async with s.get(f"{render_url}/health", timeout=ClientTimeout(total=10)) as r:
                log.debug(f"Self-ping: {r.status}")
    except Exception as e:
        log.debug(f"Self-ping failed: {e}")
