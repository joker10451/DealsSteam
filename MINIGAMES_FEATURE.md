# Мини-игры и челленджи

## Обзор

Добавлена система мини-игр для увеличения вовлечённости подписчиков канала. Пользователи могут зарабатывать баллы, участвуя в играх и челленджах.

## Функционал

### 1. Система баллов
- Каждый пользователь имеет профиль с баллами
- Баллы начисляются за правильные ответы в играх
- Таблица лидеров показывает топ-10 игроков

### 2. Игра "Угадай игру по скриншоту"
- Публикуется автоматически после обычных постов (20% шанс)
- Показывается скриншот из игры + 4 варианта ответа
- Награда: 10 баллов за правильный ответ
- Использует данные из IGDB API

### 3. Игра "Угадай цену"
- Уже существовала, интегрирована в общую систему баллов
- Награда: 5 баллов за правильный ответ

### 4. Ежедневные челленджи
- Новый челлендж каждый день
- Награда: 50 баллов за выполнение
- Типы челленджей: найти самую дешёвую игру дня и др.

## Команды бота

### Для пользователей:
- `/games` — список доступных мини-игр
- `/score` — показать свои баллы и статистику
- `/leaderboard` — таблица лидеров (топ-10)
- `/profile` — полный профиль пользователя
- `/challenge` — показать челлендж дня

## Структура БД

### Таблица `user_scores`
```sql
CREATE TABLE user_scores (
    user_id BIGINT PRIMARY KEY,
    total_score INT DEFAULT 0,
    games_played INT DEFAULT 0,
    correct_answers INT DEFAULT 0,
    last_played TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
)
```

### Таблица `screenshot_games`
```sql
CREATE TABLE screenshot_games (
    game_id TEXT PRIMARY KEY,
    correct_title TEXT NOT NULL,
    screenshot_url TEXT NOT NULL,
    options TEXT[] NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
)
```

### Таблица `screenshot_answers`
```sql
CREATE TABLE screenshot_answers (
    user_id BIGINT,
    game_id TEXT,
    answer TEXT,
    is_correct BOOLEAN,
    answered_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (user_id, game_id)
)
```

### Таблица `daily_challenges`
```sql
CREATE TABLE daily_challenges (
    challenge_date DATE PRIMARY KEY,
    challenge_type TEXT NOT NULL,
    challenge_data JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
)
```

### Таблица `daily_challenge_completions`
```sql
CREATE TABLE daily_challenge_completions (
    user_id BIGINT,
    challenge_date DATE,
    completed_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (user_id, challenge_date)
)
```

## Файлы

- `minigames.py` — логика мини-игр, работа с БД
- `handlers/games.py` — обработчики команд и callback'ов
- `publisher.py` — публикация игр в канал (функция `publish_screenshot_game`)

## Интеграция

1. Таблицы создаются автоматически при запуске бота (`database.py` → `init_minigames_db()`)
2. Роутер зарегистрирован в `handlers/__init__.py`
3. Команды добавлены в меню бота (`bot.py`)
4. Игра со скриншотом публикуется случайно после обычных постов

## Будущие улучшения

- Автоматическая генерация ежедневных челленджей
- Больше типов мини-игр (угадай жанр, угадай год выхода и т.д.)
- Награды и достижения
- Еженедельные турниры
