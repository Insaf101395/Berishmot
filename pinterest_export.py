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
import asyncio
import logging
import re
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
    for attempt in range(2):
        try:
            session = await _get_session()
            async with session.post(
                "https://api.imgbb.com/1/upload",
                data={"key": IMGBB_KEY, "image": b64},
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                data = await resp.json()
            if not data.get("success"):
                logger.error(f"ImgBB error: {data}")
                return None
            return data["data"]["url"]
        except Exception as e:
            logger.warning(f"ImgBB attempt {attempt+1} failed: {e}")
            if attempt == 0:
                await asyncio.sleep(3)
    logger.error("ImgBB: все попытки исчерпаны")
    return None


# ---------- Anthropic: фото -> русский title/description/keywords ----------
PROMPT = """You are an SEO copywriter for Pinterest. Clothing and footwear store in streetwear / y2k / old money aesthetic. English-speaking international audience.

Based on the product photo and name, return JSON in exactly this format:
{{"title": "...", "description": "...", "keywords": "..."}}

Rules:
- title: up to 90 characters, in English. Create a natural, premium-sounding Pinterest title from the item's type, silhouette, materials, color, mood, and aesthetic. Use descriptive adjectives and aesthetic terms so it sounds like an organic style description, not a technical generic label. Never reveal or hint at any brand: do not use direct names, encoded names, lookalike spellings, rearranged letters, initials, emojis, or indirect clues. Good example: "Vintage-style bomber jacket, streetwear essential". Bad example: "Stone Island Bomber" or "Bomber jacket".
- description: 1-2 sentences — what it is, how to style it, aesthetic vibe. NO calls to action ("buy", "order", "dm us"). Up to 400 characters.
- keywords: 8-10 keywords separated by commas, mix of item type + aesthetic terms (streetwear, y2k, old money, blokecore, quiet luxury, etc).
- NEVER mention brand names (Nike, Adidas, Supreme, Margiela, etc) — replace with item type or aesthetic.
- Return ONLY JSON. No markdown, no triple quotes, no explanations.

Product name from seller: {name}"""


BRAND_NAMES = [
    # Магазинные и самые распространённые бренды
    "Stone Island Shadow Project", "Stone Island", "Nike", "Adidas",
    "New Balance", "Dior", "Christian Dior", "Prada", "Gucci",
    "Balenciaga", "Off-White", "Supreme", "The North Face", "Canada Goose",
    "Moncler", "Louis Vuitton", "Burberry", "Chanel", "Fendi", "Versace",
    "Valentino", "Givenchy", "Saint Laurent", "Yves Saint Laurent",
    "Alexander McQueen", "Maison Margiela", "Margiela", "Celine", "Loewe",
    "Bottega Veneta", "Jacquemus", "Acne Studios", "Palm Angels",
    "Fear of God", "Essentials", "Amiri", "Rhude", "Kith", "Stussy",
    "Carhartt WIP", "Carhartt", "Patagonia", "Arc'teryx", "Columbia",
    "Ralph Lauren", "Polo Ralph Lauren", "Tommy Hilfiger", "Calvin Klein",
    "Lacoste", "Hugo Boss", "Boss", "Armani", "Emporio Armani",
    "Dsquared2", "Dolce & Gabbana", "Dolce Gabbana", "Miu Miu",
    "Maison Kitsune", "Kenzo", "Comme des Garcons", "Comme des Garçons",
    "A Bathing Ape", "BAPE", "Human Made", "Neighborhood", "WTAPS",
    "Undercover", "C.P. Company", "CP Company", "Loro Piana",
    "Zegna", "Brunello Cucinelli", "Lululemon", "Salomon", "Asics",
    "Puma", "Converse", "Vans", "Reebok", "Jordan", "Air Jordan",
    "Yeezy", "New Era", "UGG", "Timberland", "Dr. Martens", "Crocs",
    "Birkenstock", "Hoka", "On Running", "Skechers", "Balmain",
    "Telfar", "Michael Kors", "Coach", "Kate Spade", "Marc Jacobs",
    "Guess", "Diesel", "G-Star", "Levi's", "Wrangler", "Lee",
    "The Hundreds", "A-Cold-Wall", "A Cold Wall", "Daily Paper",
    "Represent", "Cav Empt", "Visvim", "Maharishi", "Stone Island Marina",
    "Sergio Tacchini", "Fred Perry", "Ellesse", "Fila", "Champion",
    "Under Armour", "New Balance Numeric", "Nike SB", "Adidas Originals",
    "Adidas Yeezy", "Timberland PRO", "Dr. Martens", "Veja", "Saucony",
    "Mizuno", "Onitsuka Tiger", "Asics Tiger", "The Kooples", "AllSaints",
    "Massimo Dutti", "Zara", "H&M", "Uniqlo", "COS", "Arket",
    "Abercrombie & Fitch", "Victoria's Secret", "Palm Angels",
]

_BRAND_PATTERN = re.compile(
    r"(?<!\w)(?:"
    + "|".join(re.escape(brand) for brand in sorted(BRAND_NAMES, key=len, reverse=True))
    + r")(?!\w)",
    re.IGNORECASE,
)


def _clean_anthropic_json(raw_text: str) -> str:
    """Убирает markdown fence вокруг JSON, если модель его добавила."""
    cleaned = str(raw_text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def filter_brand_names(value: object) -> str:
    """Удаляет бренды и аккуратно схлопывает оставшиеся пробелы."""
    text = str(value or "")
    text = _BRAND_PATTERN.sub("", text)
    # Убираем одиночный x/×, который часто остаётся от коллабораций
    # после удаления конструкции вроде «Stone Island x Dior».
    text = re.sub(r"(?i)(?<!\w)[x×](?!\w)", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"([,;/|])\s*(?=[,;/|])", r"\1", text)
    return text.strip(" ,;:/|-–—")


def sanitize_pin_copy(copy: object, fallback_name: str = "") -> dict:
    """Финальный защитный слой перед записью title/description/keywords."""
    data = copy if isinstance(copy, dict) else {}
    title = filter_brand_names(data.get("title") or fallback_name)
    if len(title) < 3:
        title = "Streetwear outfit"
    return {
        "title": title[:100],
        "description": filter_brand_names(data.get("description", ""))[:500],
        "keywords": filter_brand_names(data.get("keywords", "")),
    }


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
        raw = _clean_anthropic_json(msg.content[0].text)
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
    copy = sanitize_pin_copy(
        await generate_pin_copy(photo_bytes_list[0], name),
        fallback_name=name,
    )

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
