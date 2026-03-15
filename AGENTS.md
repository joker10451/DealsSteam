# Руководство для агентов — Game Deals Bot

## Настройка окружения
- Python 3.9+
- Установка зависимостей: `pip install -r requirements.txt`
- Скопируйте `.env.example` в `.env` и настройте переменные

## Команды для разработки

### Сборка
- Традиционный шаг сборки отсутствует (чистый Python)
- Инициализация БД: `python -c "from database import init_db; import asyncio; asyncio.run(init_db())"`

### Линтинг
- Flake8: `flake8 .`
- Black: `black .`
- Isort: `isort .`
- MyPy: `mypy .`

### Тестирование
- Все тесты: `pytest`
- Подробный вывод: `pytest -v`
- Конкретный файл: `pytest tests/test_database.py`
- Одна функция: `pytest tests/test_database.py::test_function_name`
- С покрытием: `pytest --cov=.`
- По ключевому слову: `pytest -k "price_parser"`

## Стиль кода

### Форматирование
- Следуйте PEP 8
- Длина строки: 88 символов (по умолчанию в black)
- 4 пробела на уровень отступа
- 2 пустые строки между определениями верхнего уровня
- 1 пустая строка между методами

### Импорты
- Порядок: стандартная библиотека, third-party, локальные
- Пустая строка между группами
- Абсолютные импорты
- Избегайте wildcards (`from module import *`)

### Type Hints
- Используйте для всех параметров и возвращаемых значений
- Встроенные коллекции (list, dict) в Python 3.9+
- Optional[T] для nullable значений

### Именование
- Переменные/функции: `snake_case`
- Классы: `PascalCase`
- Константы: `UPPER_SNAKE_CASE`
- Булевы: `is_valid`, `has_permission`

### Обработка ошибок
- Ловите конкретные исключения, не `except:`
- Логируйте с контекстом: `log = logging.getLogger(__name__)`
- Используйте правильные уровни: DEBUG, INFO, WARNING, ERROR, CRITICAL

### Документация
- docstrings: тройные кавычки, Google style
- Комментарии: объясняйте почему, а не что

## Особенности проекта

### Асинхронный код
- Правильно используйте `async`/`await`
- Избегайте блокирующих вызовов
- Используйте `asyncio.gather()` для параллельности

### База данных
- Пул соединений (asyncpg)
- Параметризованные запросы
- Явная обработка транзакций

### Внешние API
- Rate limiting
- Graceful обработка ошибок сети
- Кеширование ответов

## Git Workflow
- Основная ветка: `main`
- Ветки: `feature/`, `bugfix/`
- Сообщения коммитов: повелительное наклонение, <50 символов
- Теги: vMAJOR.MINOR.PATCH

## Render MCP Server

Проект размещён на Render. Для управления через AI-инструменты настройте MCP.

### Настройка Cursor

1. Создайте API ключ на https://dashboard.render.com/settings#api-keys
2. Добавьте в `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "render": {
      "url": "https://mcp.render.com/mcp",
      "headers": {
        "Authorization": "Bearer <ВАШ_API_KEY>"
      }
    }
  }
}
```

### Настройка Claude Code

```bash
claude mcp add --transport http render https://mcp.render.com/mcp --header "Authorization: Bearer <ВАШ_API_KEY>"
```

### Примеры команд

После настройки можно использовать:
- "Создать новую базу данных"
- "Показать сервисы"
- "Получить логи за ошибками"
- "Почему мой сайт не работает?"
- "Показать метрики за прошлый месяц"

### Поддерживаемые действия

- Сервисы: создание, список, детали, обновление переменных окружения
- Базы данных: создание, список, запросы
- Деплои: история, детали
- Логи: фильтрация
- Метрики: CPU, память, запросы

Ограничения: нельзя удалять ресурсы, только создавать и обновлять переменные окружения.

### Локальный запуск MCP

Если нужно запустить локально вместо хостинга:

1. Docker:
```json
{
  "mcpServers": {
    "render": {
      "command": "docker",
      "args": ["run", "--rm", "-e", "RENDER_API_KEY", "ghcr.io/render-oss/render-mcp-server"],
      "env": { "RENDER_API_KEY": "your_key" }
    }
  }
}
```

2. Или установить напрямую:
```bash
curl -fsSL https://raw.githubusercontent.com/render-oss/render-mcp-server/refs/heads/main/bin/install.sh | sh
```

## Развёртывание на Render

Проект уже настроен для Render:
- `bot.py` — точка входа
- `requirements.txt` — зависимости
- `Dockerfile` — контейнер
- `.env` — переменные окружения (не коммитить)

### Переменные окружения на Render

Обязательные:
- BOT_TOKEN — токен Telegram бота
- CHANNEL_ID — ID канала для постов
- ADMIN_ID — ID админа
- DATABASE_URL — строка подключения к PostgreSQL

Опциональные:
- IGDB_CLIENT_ID, IGDB_CLIENT_SECRET
- RAWG_API_KEY
- STEAM_API_KEY
- MIN_DISCOUNT_PERCENT (по умолчанию 50)
- MIN_STEAM_RATING (по умолчанию 70)

### Мониторинг

- Логи смотрите в Dashboard Render -> Your Service -> Logs
- Метрики: Dashboard Render -> Your Service -> Metrics
- После деплоя бот запускается автоматически по расписанию из bot.py