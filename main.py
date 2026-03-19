#!/usr/bin/env python3
import html
import json
import re
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / 'config.json'
STARTUP_TIME = datetime.now(timezone.utc).timestamp()
PROMPT = """Ты помощник по нормализации быстрых заметок для Telegram-бота.
Верни строго JSON без markdown-обертки и без пояснений.

Нужно вернуть объект с полями:
- title: это строго первая строка исходного текста; не изменяй ее и не дополняй
- obsidian_md: markdown-заметка для Obsidian
- apple_notes_text: исправленный текст заметки без заголовка, только тело
- summary: короткое предложение о содержании заметки, оканчивающееся словом "записано" или "записана"

Правила:
- Не выдумывай факты.
- Исправляй орфографические, пунктуационные и явные грамматические ошибки в теле заметки.
- Заголовок всегда равен первой строке исходного текста.
- Всё после первой строки исходного текста относится только к телу заметки.
- В apple_notes_text верни только исправленное тело заметки, без заголовка.
- Для obsidian_md используй такой формат:
  # <title>

  <исправленное тело заметки>
"""


@dataclass
class NormalizedNote:
    title: str
    obsidian_md: str
    apple_notes_text: str
    summary: str


def extract_title(raw_text: str) -> str:
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if not lines:
        return 'Заметка'
    return lines[0][:60] or 'Заметка'


def raw_body_without_title(raw_text: str) -> str:
    lines = raw_text.splitlines()
    if len(lines) <= 1:
        return ''
    return '\n'.join(lines[1:]).strip()


def append_note_date(text: str) -> str:
    stamp = datetime.now().strftime('%d.%m.%Y')
    cleaned = text.rstrip()
    if not cleaned:
        return stamp
    return f"{cleaned}\n\n{stamp}"


def build_ascii_title(title: str) -> str:
    clean = ' '.join(title.split()).strip() or 'Заметка'
    side_padding = 4
    ornament = '୨ৎ'
    inner_width = max((side_padding * 2) + len(clean), len(ornament) + 2, 4)

    total_padding = inner_width - len(clean)
    left_padding = (total_padding + 1) // 2
    right_padding = total_padding - left_padding
    middle = (' ' * left_padding) + clean + (' ' * right_padding)
    top = '┌' + ('─' * inner_width) + '┐'

    left_bottom = (inner_width - len(ornament)) // 2
    right_bottom = inner_width - len(ornament) - left_bottom
    bottom = '└' + ('─' * left_bottom) + ornament + ('─' * right_bottom) + '┘'

    return '\n'.join(
        f'<code>{html.escape(line)}</code>'
        for line in (top, middle, bottom)
    )


def build_note_preview(note: NormalizedNote) -> str:
    title = note.title.strip() or 'Заметка'
    body = note.apple_notes_text.strip()
    framed_title = build_ascii_title(title)
    if body:
        indented_body = '\n'.join(
            f'  {html.escape(line)}' if line else ''
            for line in body.splitlines()
        )
        return f"{framed_title}\n\n{indented_body}"
    return framed_title


def enforce_title_and_date(note: NormalizedNote, raw_text: str) -> NormalizedNote:
    title = extract_title(raw_text)
    normalized_body = note.apple_notes_text.strip() or raw_body_without_title(raw_text)
    normalized_lines = normalized_body.splitlines()
    if normalized_lines and normalized_lines[0].strip() == title:
        normalized_body = '\n'.join(normalized_lines[1:]).strip()

    obsidian_body = f'# {title}'
    if normalized_body:
        obsidian_body += f'\n\n{normalized_body}'

    return NormalizedNote(
        title=title,
        obsidian_md=append_note_date(obsidian_body) + '\n',
        apple_notes_text=append_note_date(normalized_body),
        summary=note.summary,
    )


def load_config() -> Dict[str, Any]:
    with CONFIG_PATH.open('r', encoding='utf-8') as fh:
        return json.load(fh)


CONFIG = load_config()
TELEGRAM_TOKEN = CONFIG['telegram_bot_token']
LLM_PROVIDER = CONFIG.get('llm_provider', 'mistral')
LLM_API_KEY = CONFIG['llm_api_key']
OBSIDIAN_DIR = Path(CONFIG['obsidian_notes_dir']).expanduser()
APPLE_FOLDER = CONFIG.get('apple_notes_folder')
DEFAULT_MODEL = CONFIG.get('llm_model', 'mistral-small-latest')
POLL_INTERVAL_SECONDS = int(CONFIG.get('poll_interval_seconds', 2))
DEFAULT_CHAT_ID = CONFIG.get('default_chat_id')
TELEGRAM_API = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}'
MISTRAL_URL = 'https://api.mistral.ai/v1/chat/completions'


def json_request(url: str, payload: Optional[dict] = None, timeout: int = 60, headers: Optional[dict] = None) -> dict:
    data = None
    merged_headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        merged_headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=merged_headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


def telegram_api(method: str, payload: Optional[dict] = None) -> dict:
    return json_request(f'{TELEGRAM_API}/{method}', payload=payload)


def send_message(chat_id: int, text: str, parse_mode: Optional[str] = None) -> None:
    payload = {'chat_id': chat_id, 'text': text}
    if parse_mode:
        payload['parse_mode'] = parse_mode
    telegram_api('sendMessage', payload)


def get_updates(offset: Optional[int]) -> dict:
    payload = {'timeout': 30}
    if offset is not None:
        payload['offset'] = offset
    return telegram_api('getUpdates', payload)


def parse_llm_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith('```'):
        cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
        cleaned = re.sub(r'\s*```$', '', cleaned)
    return json.loads(cleaned)


def call_mistral(raw_text: str) -> NormalizedNote:
    title = extract_title(raw_text)
    body = raw_body_without_title(raw_text)
    payload = {
        'model': DEFAULT_MODEL,
        'temperature': 0.2,
        'messages': [
            {'role': 'system', 'content': PROMPT},
            {
                'role': 'user',
                'content': (
                    f'Текущая локальная дата и время: {datetime.now().astimezone().isoformat()}\n\n'
                    f'Заголовок заметки:\n{title}\n\n'
                    f'Тело заметки:\n{body}'
                ),
            },
        ],
        'response_format': {'type': 'json_object'},
    }
    response = json_request(
        MISTRAL_URL,
        payload=payload,
        timeout=90,
        headers={'Authorization': f'Bearer {LLM_API_KEY}'},
    )
    try:
        content = response['choices'][0]['message']['content']
        if isinstance(content, list):
            content = ''.join(part.get('text', '') for part in content if isinstance(part, dict))
        data = parse_llm_json(content)
    except Exception as exc:
        raise RuntimeError(f'Mistral returned an unexpected payload: {response}') from exc

    summary = (data.get('summary') or 'Заметка записана').strip()
    apple_notes_text = (data.get('apple_notes_text') or body).strip()
    if apple_notes_text.startswith(title):
        apple_notes_text = apple_notes_text[len(title):].lstrip('\n ').strip()
    obsidian_md = (data.get('obsidian_md') or f'# {title}\n\n{apple_notes_text}\n').strip() + '\n'
    return NormalizedNote(
        title=title,
        obsidian_md=obsidian_md,
        apple_notes_text=apple_notes_text,
        summary=summary,
    )


def call_llm(raw_text: str) -> NormalizedNote:
    if LLM_PROVIDER != 'mistral':
        raise RuntimeError(f'Unsupported llm_provider: {LLM_PROVIDER}')
    return call_mistral(raw_text)


def safe_slug(text: str) -> str:
    text = re.sub(r'[^0-9A-Za-zА-Яа-яЁё _-]+', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:50] or 'note'


def write_obsidian(note: NormalizedNote) -> Path:
    OBSIDIAN_DIR.mkdir(parents=True, exist_ok=True)
    stem = safe_slug(note.title)
    path = OBSIDIAN_DIR / f'{stem}.md'
    counter = 2
    while path.exists():
        path = OBSIDIAN_DIR / f'{stem} ({counter}).md'
        counter += 1
    path.write_text(note.obsidian_md, encoding='utf-8')
    return path


def escape_applescript(value: str) -> str:
    return value.replace('\\', '\\\\').replace('"', '\\"')


def plain_text_to_notes_html(text: str) -> str:
    escaped = html.escape(text)
    return escaped.replace('\n', '<br>')


def create_apple_note(note: NormalizedNote) -> None:
    title = escape_applescript(note.title)
    body = escape_applescript(plain_text_to_notes_html(note.apple_notes_text))
    if APPLE_FOLDER:
        folder = escape_applescript(APPLE_FOLDER)
        script = f'''
        set noteTitle to "{title}"
        set noteBody to "{body}"
        set targetFolderName to "{folder}"
        tell application "Notes"
            if not running then launch
            delay 0.2
            set targetAccount to first account
            set targetFolder to missing value
            repeat with f in folders of targetAccount
                if name of f is targetFolderName then
                    set targetFolder to f
                    exit repeat
                end if
            end repeat
            if targetFolder is missing value then
                set targetFolder to make new folder at targetAccount with properties {{name:targetFolderName}}
            end if
            make new note at targetFolder with properties {{name:noteTitle, body:noteBody}}
        end tell
        '''
    else:
        script = f'''
        set noteTitle to "{title}"
        set noteBody to "{body}"
        tell application "Notes"
            if not running then launch
            delay 0.2
            set targetAccount to first account
            make new note at targetAccount with properties {{name:noteTitle, body:noteBody}}
        end tell
        '''
    subprocess.run(['/usr/bin/osascript', '-e', script], check=True)


def handle_help(chat_id: int) -> None:
    send_message(
        chat_id,
        'Команды:\n'
        '/start - приветствие\n'
        '/help - помощь\n'
        '/note <текст> - сохранить заметку\n\n'
        'Можно просто прислать обычный текст: бот нормализует его, создаст заметку в Apple Notes и файл в Obsidian.',
    )


def process_note(chat_id: int, raw_text: str) -> None:
    send_message(chat_id, 'Обрабатываю заметку...')
    try:
        note = call_llm(raw_text)
    except Exception:
        print('LLM normalization failed, aborting save', file=sys.stderr)
        traceback.print_exc()
        send_message(chat_id, 'Не удалось обработать заметку через ИИ. Запись не выполнена.')
        return

    note = enforce_title_and_date(note, raw_text)
    create_apple_note(note)
    write_obsidian(note)

    summary_line = ' '.join(note.summary.split()).strip()
    summary_line = summary_line.replace('сохранена', 'записана').replace('нормализована.', 'записана.').replace('нормализована', 'записана')
    note_preview = build_note_preview(note)

    status_response = (
        f'<blockquote>{html.escape(summary_line)}</blockquote>\n'
        'Apple Notes: ✅\n'
        'Obsidian: ✅'
    )
    preview_response = f'<blockquote expandable>{note_preview}</blockquote>'

    send_message(chat_id, status_response, parse_mode='HTML')
    send_message(chat_id, preview_response, parse_mode='HTML')


def extract_text(update: dict) -> Optional[Tuple[int, str]]:
    message = update.get('message')
    if not message:
        return None
    message_date = message.get('date')
    if isinstance(message_date, int) and message_date < STARTUP_TIME - 2:
        return None
    text = message.get('text')
    if not text:
        return None
    return message['chat']['id'], text.strip()


def handle_update(update: dict) -> None:
    extracted = extract_text(update)
    if not extracted:
        return
    chat_id, text = extracted
    if text.startswith('/start'):
        send_message(chat_id, 'Бот готов. Присылай заметки обычным текстом или используй /help.')
        return
    if text.startswith('/help'):
        handle_help(chat_id)
        return
    if text.startswith('/note'):
        text = text.replace('/note', '', 1).strip()
        if not text:
            send_message(chat_id, 'После /note нужен текст заметки.')
            return
    process_note(chat_id, text)


def main() -> None:
    offset = None
    print('Bot is running')
    if DEFAULT_CHAT_ID:
        print(f'Default chat id from config: {DEFAULT_CHAT_ID}')
    while True:
        try:
            updates = get_updates(offset)
            for item in updates.get('result', []):
                offset = item['update_id'] + 1
                try:
                    handle_update(item)
                except Exception:
                    print('Update handling error', file=sys.stderr)
                    traceback.print_exc()
        except urllib.error.HTTPError as exc:
            print(f'HTTP error: {exc}', file=sys.stderr)
            time.sleep(5)
        except urllib.error.URLError as exc:
            print(f'Network error: {exc}', file=sys.stderr)
            time.sleep(5)
        except KeyboardInterrupt:
            print('Stopping bot')
            break
        except Exception:
            print('Unexpected loop error', file=sys.stderr)
            traceback.print_exc()
            time.sleep(5)
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == '__main__':
    main()
