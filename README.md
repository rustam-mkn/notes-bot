# Telegram Notes Bot

Телеграм-бот для заметок на Python без внешних Python-зависимостей.

## Что делает бот

- принимает заметки из Telegram через long polling
- нормализует текст через Mistral API
- сохраняет Markdown-заметки в Obsidian
- создаёт заметки в Apple Notes через `osascript`

## Зависимости

У проекта нет `pip`-зависимостей и нет `requirements.txt`: используется только стандартная библиотека Python.

Для работы нужны:
- Python 3.10+
- macOS с приложением Apple Notes
- `osascript` (входит в macOS)
- токен Telegram-бота
- API-ключ Mistral
- существующее хранилище Obsidian или папка для Markdown-файлов

## Настройка

1. Создайте `config.json` на основе `config.example.json` и заполните его своими значениями.
2. Убедитесь, что заданы поля:
   - `telegram_bot_token`
   - `llm_api_key`
   - `obsidian_notes_dir`
   - при необходимости: `apple_notes_folder`, `llm_model`, `poll_interval_seconds`, `default_chat_id`
3. Откройте Apple Notes хотя бы один раз на этом Mac. Если нужна синхронизация с iPhone, убедитесь, что включены iCloud Notes.

## Запуск

```bash
cd /Users/user/Dev/bot/notes
python3 main.py
```

## Команды

- `/start`
- `/help`
- `/note текст заметки`

Обычные текстовые сообщения тоже обрабатываются как заметки.

## Apple Notes

Бот сохраняет заметки либо в корень вашей основной учётной записи Apple Notes, либо в папку из `apple_notes_folder`, если она указана в конфиге.
