# Автоматические ежедневные челленджи

## Что это?

Система автоматически генерирует и публикует ежедневные челленджи для пользователей. Челленджи мотивируют активность и дают дополнительные баллы.

## Типы челленджей

1. **find_cheapest** — Найди самую дешёвую игру дня
   - Награда: 50 баллов
   - Подсказка: цена меньше N₽

2. **guess_streak** — Угадай N игр подряд
   - Награда: 50 баллов
   - Требуется: 3 или 5 правильных ответов подряд

3. **daily_score** — Набери N баллов за день
   - Награда: 50 баллов
   - Цель: 30, 50, 75 или 100 баллов

4. **vote_games** — Проголосуй за N игр
   - Награда: 50 баллов
   - Требуется: 3, 5 или 10 голосов

5. **find_genre** — Найди игру определённого жанра
   - Награда: 50 баллов
   - Жанры: RPG, Action, Strategy, Horror, Indie, Roguelike, Shooter

6. **find_discount** — Найди игру со скидкой больше N%
   - Награда: 50 баллов
   - Минимальная скидка: 70%, 80% или 90%

## Расписание

- **00:00 МСК** — Генерация нового челленджа
- **09:00 МСК** — Публикация челленджа в канал

## Команды

- `/challenge` — Посмотреть челлендж дня и свой прогресс

## Прогресс

Система автоматически отслеживает прогресс для челленджей:

- **daily_score** — считает баллы за сегодня
- **vote_games** — считает голоса за сегодня
- Остальные типы требуют ручной проверки

## Технические детали

### Файлы

- `daily_challenges.py` — генерация и публикация челленджей
- `minigames.py` — хранение и проверка выполнения
- `handlers/games.py` — команда `/challenge`

### База данных

**Таблица `daily_challenges`:**
```sql
CREATE TABLE daily_challenges (
    challenge_date DATE PRIMARY KEY,
    challenge_type TEXT NOT NULL,
    challenge_data JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
)
```

**Таблица `daily_challenge_completions`:**
```sql
CREATE TABLE daily_challenge_completions (
    user_id BIGINT,
    challenge_date DATE,
    completed_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (user_id, challenge_date)
)
```

**Таблица `user_score_history`:**
```sql
CREATE TABLE user_score_history (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    points INT NOT NULL,
    reason TEXT,
    earned_at TIMESTAMPTZ DEFAULT NOW()
)
```

### Функции

**Генерация:**
- `generate_daily_challenge()` — создаёт случайный челлендж
- `create_todays_challenge()` — сохраняет челлендж в БД
- `publish_daily_challenge()` — публикует в канал

**Проверка:**
- `check_challenge_progress(user_id)` — проверяет прогресс
- `complete_daily_challenge(user_id)` — отмечает как выполненный

**Форматирование:**
- `format_challenge_message(challenge)` — форматирует сообщение

## Добавление новых типов

1. Добавь тип в `CHALLENGE_TYPES` в `daily_challenges.py`
2. Добавь генерацию в `generate_daily_challenge()`
3. Добавь проверку прогресса в `check_challenge_progress()`
4. Добавь форматирование в `format_challenge_message()`

Пример:
```python
elif challenge_type == "new_type":
    return {
        "type": "new_type",
        "data": {
            "param": value,
            "description": "Описание челленджа"
        }
    }
```

## Тестирование

Запуск тестов:
```bash
pytest tests/test_daily_challenges.py -v
```

Все тесты проверяют:
- Генерацию челленджей
- Сохранение в БД
- Проверку прогресса
- Форматирование сообщений
- Выполнение челленджей

## Мониторинг

Проверяй логи на Render.com:
- Генерация челленджа в 00:00
- Публикация в 09:00
- Ошибки при создании/публикации

## Будущие улучшения

- Автоматическая проверка выполнения для всех типов
- Уведомления о выполнении челленджа
- Статистика по челленджам
- Специальные челленджи на выходные
- Челленджи с увеличенной наградой
