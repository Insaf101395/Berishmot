"""
pinterest_export.py
Модуль для бота Berishmot: товар из TG -> ImgBB -> локальный текст -> строка в CSV.
В конце ты вызываешь /export и заливаешь готовый CSV в Pinterest (Настройки -> Импорт контента).
Никакого Pinterest API / OAuth / Standard не нужно.
"""

import csv
import os
import base64
import asyncio
import logging
import re
from datetime import datetime, timedelta

import aiohttp
import title_gen

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

PINS_PER_PRODUCT = 1               # сколько первых фото -> сколько пинов на товар
CSV_PATH = "pinterest_batch.csv"   # текущая партия
TG_LINK = "https://berishmot.store/"   # базовая ссылка под каждым пином

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


_used_titles: set[str] = set()


async def generate_pin_copy(
    image_bytes: bytes,
    name: str,
    material: str = "",
) -> dict:
    title = title_gen.generate_title(name, material, _used_titles)
    _used_titles.add(title)
    return {
        "title": title,
        "description": title_gen.generate_description(name, material),
        "keywords": title_gen.generate_keywords(name, material),
    }


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


_pin_link_counter = _count()


def _next_pin_link() -> str:
    global _pin_link_counter
    _pin_link_counter += 1
    return f"{TG_LINK}?pin={_pin_link_counter}"


def normalize_media_url(url: str) -> str:
    """Меняет только конечное расширение .jpg на допустимое Pinterest .jpeg."""
    return re.sub(r"\.jpg$", ".jpeg", str(url or ""), flags=re.IGNORECASE)


# ---------- Главная функция: добавить товар ----------
async def add_product(
    photo_bytes_list: list[bytes],
    name: str,
    category: str,
    material: str = "",
) -> tuple[int, int]:
    """
    photo_bytes_list — байты первых фото товара (берём первые PINS_PER_PRODUCT).
    Возвращает (добавлено_строк, всего_в_партии).
    """
    if not photo_bytes_list:
        return 0, _count()

    board = BOARD_MAP.get(category, DEFAULT_BOARD)
    copy = sanitize_pin_copy(
        await generate_pin_copy(photo_bytes_list[0], name, material),
        fallback_name=name,
    )

    rows = []
    for img in photo_bytes_list[:PINS_PER_PRODUCT]:
        url = await upload_to_imgbb(img)
        if not url:
            continue
        media_url = normalize_media_url(url)
        rows.append({
            "Title": copy.get("title", name)[:100],
            "Media URL": media_url,
            "Pinterest board": board,
            "Thumbnail": "",
            "Description": copy.get("description", "")[:500],
            "Link": _next_pin_link(),
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
