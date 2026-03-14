"""
Общие утилиты для парсеров.
"""
import asyncio
import aiohttp
from typing import Optional, Union

# Глобальная сессия — инициализируется при старте бота через init_session()
_session: Optional[aiohttp.ClientSession] = None


def get_session() -> Optional[aiohttp.ClientSession]:
    return _session


async def init_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


async def close_session():
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None


async def fetch_with_retry(
    url: str,
    retries: int = 3,
    delay: float = 2.0,
    headers: Optional[dict] = None,
    params: Optional[dict] = None,
    as_json: bool = True,
) -> Optional[Union[str, dict, list]]:
    """Выполняет GET-запрос с повторными попытками при ошибках.
    Использует глобальную сессию если доступна, иначе создаёт временную.
    """
    session = get_session()
    _own_session = False
    if session is None or session.closed:
        session = aiohttp.ClientSession()
        _own_session = True

    try:
        for attempt in range(retries):
            try:
                req_headers = {**(headers or {})}
                async with session.get(
                    url,
                    headers=req_headers,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        if as_json:
                            return await resp.json(content_type=None)
                        return await resp.text()
            except Exception:
                pass
            if attempt < retries - 1:
                await asyncio.sleep(delay * (attempt + 1))
    finally:
        if _own_session:
            await session.close()

    return None
