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
from datetime import datetime

import aiohttp
from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)

# ====================== НАСТРОЙКИ (меняешь под себя) ======================
IMGBB_KEY = os.getenv("IMGBB_KEY")                  # ключ с imgbb.com -> About -> API
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")  # твой ключ Anthropic

MODEL = "claude-3-5-sonnet-20241022"  # качество. Дешевле: "claude-3-haiku-20240307"
PINS_PER_PRODUCT = 1               # сколько первых фото -> сколько пинов на товар (1 = безопасно, без near-duplicate)
CSV_PATH = "pinterest_batch.csv"   # текущая партия
TG_LINK = "https://t.me/+0uo05xuDQ1M2NWVi"   # ссылка-воронка под каждым пином

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
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.imgbb.com/1/upload",
                data={"key": IMGBB_KEY, "image": b64},
            ) as resp:
                data = await resp.json()
        if not data.get("success"):
            logger.error(f"ImgBB error: {data}")
            return None
        return data["data"]["url"]          # прямая ссылка вида https://i.ibb.co/.../file.jpg
    except Exception as e:
        logger.error(f"ImgBB exception: {e}")
        return None


# ---------- Anthropic: фото -> русский title/description/keywords ----------
PROMPT = """Ты — SEO-копирайтер для Pinterest. Рынок русскоязычный. Магазин одежды и обуви в стиле streetwear / y2k / old money.

По фото товара и названию верни JSON строго такого вида:
{{"title": "...", "description": "...", "keywords": "..."}}

Правила:
- title: до 90 символов, на русском, начинается с типа вещи + стиль. English-эстетику можно (old money, y2k, streetwear, blokecore).
- description: 1-2 предложения — что за вещь и с чем носить + эстетика. БЕЗ призывов "заказать/купить/пиши". До 400 символов.
- keywords: 8-10 ключей через запятую, русские + английские эстетик-термины.
- НИКОГДА не упоминай бренды (Supreme, Nike, Adidas, Margiela и любые другие) — заменяй на тип вещи или эстетику.
- Верни ТОЛЬКО JSON. Без markdown, без тройных кавычек, без пояснений.

Название товара от продавца: {name}"""


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
        return {"title": (name or "Образ streetwear")[:90],
                "description": "Стильная вещь для streetwear образа. Сочетается с базовыми вещами.",
                "keywords": "streetwear, y2k, образ, лук, оверсайз, унисекс, стрит стиль"}


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
        rows.append({
            "Title": copy.get("title", name)[:100],
            "Media URL": url,
            "Pinterest board": board,
            "Thumbnail": "",
            "Description": copy.get("description", "")[:500],
            "Link": TG_LINK,
            "Publish date": "",
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
