"""
Общие утилиты для парсеров.
"""
import asyncio
import logging
import aiohttp
from typing import Optional, Union

log = logging.getLogger(__name__)

# Глобальная сессия — инициализируется при старте бота через init_session()
_session: Optional[aiohttp.ClientSession] = None

# Задержки между попытками (секунды): быстрые ретраи → длинные паузы
# Индекс = номер попытки (0-based). Последнее значение используется для всех дальнейших.
_RETRY_DELAYS = [2.0, 60.0, 300.0]  # 2с → 1 мин → 5 мин


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

    Стратегия задержек (parser-friendly backoff):
      попытка 1 → сразу
      попытка 2 → 2 сек  (быстрый ретрай на флуктуацию сети)
      попытка 3 → 60 сек (ждём минуту — сайт мог временно лечь)
      попытка 4+ → 300 сек (5 минут — серьёзный сбой)

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
                async with session.get(
                    url,
                    headers=headers or {},
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        if as_json:
                            return await resp.json(content_type=None)
                        return await resp.text()
                    # 429 Too Many Requests — ждём дольше
                    if resp.status == 429:
                        retry_after = int(resp.headers.get("Retry-After", 60))
                        log.warning(f"429 от {url}, ждём {retry_after}с")
                        await asyncio.sleep(retry_after)
                        continue
                    # 5xx — сервер лежит, смысл ретраить есть
                    if resp.status >= 500:
                        log.warning(f"HTTP {resp.status} от {url} (попытка {attempt+1}/{retries})")
                    else:
                        # 4xx (кроме 429) — ретраить бессмысленно
                        log.debug(f"HTTP {resp.status} от {url}, не ретраим")
                        return None
            except asyncio.TimeoutError:
                log.warning(f"Таймаут {url} (попытка {attempt+1}/{retries})")
            except aiohttp.ClientError as e:
                log.warning(f"Сетевая ошибка {url} (попытка {attempt+1}/{retries}): {e}")
            except Exception as e:
                log.warning(f"Неожиданная ошибка {url} (попытка {attempt+1}/{retries}): {e}")

            if attempt < retries - 1:
                sleep_time = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                log.info(f"Следующая попытка через {sleep_time:.0f}с...")
                await asyncio.sleep(sleep_time)
    finally:
        if _own_session:
            await session.close()

    log.error(f"Все {retries} попытки исчерпаны для {url}")
    return None
