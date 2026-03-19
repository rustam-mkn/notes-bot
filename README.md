# Telegram Notes Bot

Self-contained Telegram bot on Python standard library.

What it does:
- accepts notes from Telegram via long polling
- normalizes text through Mistral API
- saves Markdown notes to Obsidian
- creates notes in Apple Notes via `osascript`

## Run

Create `config.json` from `config.example.json` and fill in your own tokens/paths.

```bash
cd /Users/user/Dev/bot/notes
python3 main.py
```

## Commands

- `/start`
- `/help`
- `/note текст заметки`

Plain text messages are treated as notes automatically.

## Apple Notes

The bot saves notes directly into the main root of your default Apple Notes account, so they appear alongside your existing notes and sync to iPhone through iCloud Notes.
