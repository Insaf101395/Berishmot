"""
pinterest_export.py
Модуль для бота Berishmot: товар из TG -> ImgBB -> Anthropic (русский текст) -> строка в CSV.
В конце ты вызываешь /export и заливаешь готовый CSV в Pinterest (Настройки -> Импорт контента).
Никакого Pinterest API / OAuth / Standard не нужно.
"""

import csv
import json
import os
import base64
import logging
from datetime import datetime, timedelta

import aiohttp
from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)

# Переиспользуемая сессия (создаётся при первом запросе)
_http_session: aiohttp.ClientSession | None = None

async def _get_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession()
    return _http_session

# ====================== НАСТРОЙКИ (меняешь под себя) ======================
IMGBB_KEY = os.getenv("IMGBB_KEY")                  # ключ с imgbb.com -> About -> API
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")  # твой ключ Anthropic

MODEL = "claude-3-5-sonnet-20241022"  # качество. Дешевле: "claude-3-haiku-20240307"
PINS_PER_PRODUCT = 1               # сколько первых фото -> сколько пинов на товар
CSV_PATH = "pinterest_batch.csv"   # текущая партия
TG_LINK = "https://t.me/+0uo05xuDQ1M2NWVi"   # ссылка-воронка под каждым пином

# --- Расписание пинов ---
PIN_INTERVAL_MINUTES = 30   # интервал между пинами
PINS_PER_DAY = 30           # лимит пинов в сутки
PUBLISH_START_HOUR = 9      # начало публикаций (час)
PUBLISH_END_HOUR = 24       # конец публикаций (час, 24 = полночь)

CSV_HEADERS = ["Title", "Media URL", "Pinterest board", "Thumbnail",
               "Description", "Link", "Publish date", "Keywords"]

# категория из твоего бота -> доска Pinterest (правь как удобно)
BOARD_MAP = {
    "Обувь Adidas": "Кроссовки",
    "Обувь Nike": "Кроссовки",
    "Обувь New Balance": "Кроссовки",
    "Обувь Микс": "Кроссовки",
    "Костюмы": "Костюмы",
    "Old Money": "Old Money",
    "Зима": "Зимняя одежда",
    "Худи и Свитшоты": "Худи и свитшоты",
    "Футболки и Рубашки": "Футболки и рубашки",
    "Куртки и Ветровки": "Куртки и ветровки",
    "Штаны и Джинсы": "Штаны и джинсы",
    "Шорты и Трусы": "Шорты",
    "Головные уборы и Шарфы": "Головные уборы",
    "Аксессуары": "Аксессуары",
    "Сумки и Кошельки": "Сумки и кошельки",
}
DEFAULT_BOARD = "Streetwear"
# =========================================================================

client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


# ---------- ImgBB: фото -> прямая публичная ссылка ----------
async def upload_to_imgbb(image_bytes: bytes) -> str | None:
    b64 = base64.b64encode(image_bytes).decode()
    try:
        session = await _get_session()
        async with session.post(
            "https://api.imgbb.com/1/upload",
            data={"key": IMGBB_KEY, "image": b64},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            data = await resp.json()
        if not data.get("success"):
            logger.error(f"ImgBB error: {data}")
            return None
        return data["data"]["url"]
    except Exception as e:
        logger.error(f"ImgBB exception: {e}")
        return None


# ---------- Anthropic: фото -> русский title/description/keywords ----------
PROMPT = """You are an SEO copywriter for Pinterest. Clothing and footwear store in streetwear / y2k / old money aesthetic. English-speaking international audience.

Based on the product photo and name, return JSON in exactly this format:
{{"title": "...", "description": "...", "keywords": "..."}}

Rules:
- title: up to 90 characters, in English, start with item type + style/aesthetic.
- description: 1-2 sentences — what it is, how to style it, aesthetic vibe. NO calls to action ("buy", "order", "dm us"). Up to 400 characters.
- keywords: 8-10 keywords separated by commas, mix of item type + aesthetic terms (streetwear, y2k, old money, blokecore, quiet luxury, etc).
- NEVER mention brand names (Nike, Adidas, Supreme, Margiela, etc) — replace with item type or aesthetic.
- Return ONLY JSON. No markdown, no triple quotes, no explanations.

Product name from seller: {name}"""


async def generate_pin_copy(image_bytes: bytes, name: str) -> dict:
    b64 = base64.b64encode(image_bytes).decode()
    try:
        msg = await client.messages.create(
            model=MODEL,
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64",
                     "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": PROMPT.format(name=name or "не указано")},
                ],
            }],
        )
        raw = msg.content[0].text.strip().replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        logger.error(f"Anthropic/JSON fail: {e}")
        # запасной вариант, чтобы пайплайн не падал
        return {"title": (name or "Streetwear outfit")[:90],
                "description": "Stylish streetwear piece. Easy to style with everyday basics for a clean, modern look.",
                "keywords": "streetwear, y2k, old money, outfit, oversized, unisex, street style, aesthetic, fashion"}


# ---------- Расписание ----------
def _get_next_publish_time() -> str:
    """Вычисляет следующий слот публикации: последний в CSV + 30 мин, не более 30 в день."""
    last_dt: datetime | None = None
    counts: dict = {}   # date -> количество пинов

    if os.path.exists(CSV_PATH):
        with open(CSV_PATH, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                raw = row.get("Publish date", "").strip()
                if not raw:
                    continue
                try:
                    dt = datetime.fromisoformat(raw)
                    day = dt.date()
                    counts[day] = counts.get(day, 0) + 1
                    if last_dt is None or dt > last_dt:
                        last_dt = dt
                except ValueError:
                    pass

    now = datetime.now()

    if last_dt is None:
        # Первый пин — стартуем с текущего часа (не раньше PUBLISH_START_HOUR)
        base = now.replace(minute=0, second=0, microsecond=0)
        if base.hour < PUBLISH_START_HOUR:
            base = base.replace(hour=PUBLISH_START_HOUR)
        return base.strftime("%Y-%m-%dT%H:%M:%S")

    next_dt = last_dt + timedelta(minutes=PIN_INTERVAL_MINUTES)

    # Если вышли за конец дня или превысили лимит — переносим на следующий день
    next_day = next_dt.date()
    if next_dt.hour >= PUBLISH_END_HOUR or counts.get(next_day, 0) >= PINS_PER_DAY:
        next_day = next_day + timedelta(days=1)
        next_dt = datetime(next_day.year, next_day.month, next_day.day, PUBLISH_START_HOUR, 0, 0)

    return next_dt.strftime("%Y-%m-%dT%H:%M:%S")


# ---------- CSV ----------
def _append_rows(rows: list[dict]):
    new_file = not os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_HEADERS, quoting=csv.QUOTE_MINIMAL)
        if new_file:
            w.writeheader()
        for r in rows:
            w.writerow(r)


def _count() -> int:
    if not os.path.exists(CSV_PATH):
        return 0
    with open(CSV_PATH, encoding="utf-8-sig") as f:
        return max(0, sum(1 for _ in f) - 1)   # минус строка заголовка


# ---------- Главная функция: добавить товар ----------
async def add_product(photo_bytes_list: list[bytes], name: str, category: str) -> tuple[int, int]:
    """
    photo_bytes_list — байты первых фото товара (берём первые PINS_PER_PRODUCT).
    Возвращает (добавлено_строк, всего_в_партии).
    """
    if not photo_bytes_list:
        return 0, _count()

    board = BOARD_MAP.get(category, DEFAULT_BOARD)
    copy = await generate_pin_copy(photo_bytes_list[0], name)   # текст один раз по первому фото

    rows = []
    for img in photo_bytes_list[:PINS_PER_PRODUCT]:
        url = await upload_to_imgbb(img)
        if not url:
            continue
        publish_at = _get_next_publish_time()
        rows.append({
            "Title": copy.get("title", name)[:100],
            "Media URL": url,
            "Pinterest board": board,
            "Thumbnail": "",
            "Description": copy.get("description", "")[:500],
            "Link": TG_LINK,
            "Publish date": publish_at,
            "Keywords": copy.get("keywords", ""),
        })

    if rows:
        _append_rows(rows)
    return len(rows), _count()


# ---------- Команды бота ----------
def get_csv_path() -> str | None:
    return CSV_PATH if os.path.exists(CSV_PATH) else None


def count_batch() -> int:
    return _count()


def clear_batch() -> str | None:
    """Архивирует текущую партию (переименовывает с датой), начинает пустую."""
    if not os.path.exists(CSV_PATH):
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    archived = f"pinterest_batch_{stamp}.csv"
    os.rename(CSV_PATH, archived)
    return archived
