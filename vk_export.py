import os
import io
import json
import base64
import asyncio
import logging
from datetime import datetime
from xml.sax.saxutils import escape as _xml_escape

logger = logging.getLogger(__name__)

# ============ НАСТРОЙКИ (правь тут) ============
SHOP_NAME = "Berishmot Store"
SHOP_URL = "https://vk.com/berishmotvk"      # <shop><url>
PRODUCT_URL = "https://vk.com/berishmotvk"   # <offer><url> — куда ведёт карточка

# Ссылки как на скринах 1-2
REVIEWS_URL = "https://vk.com/topic-147969195_35462100?post=7351"
GUARANTEES_URL = "https://vk.com/topic-147969195_35466647"

# Эмодзи-стикеры как на скринах
EMOJI_LEAF = "🌿"   # размеры / материал
EMOJI_GEM = "💎"    # отзывы / гарантии

# Файлы партии
VK_STORE = "vk_batch.jsonl"   # накопитель offer'ов (по строке JSON на товар)
VK_YML = "vk_batch.xml"       # готовый файл для импорта в VK (YML внутри, расширение .xml)

# Категории VK (шапка YML)
VK_CATEGORIES = [
    (1, "Обувь"),
    (2, "Одежда"),
    (3, "Аксессуары"),
    (4, "Сумки и кошельки"),
]


# ============ КАТЕГОРИИ: бот -> VK categoryId ============
def _category_id(bot_category: str) -> int:
    """Маппинг категории из CATEGORY_MAP бота в VK categoryId (1-4)."""
    c = (bot_category or "").strip().lower()
    if c.startswith("обувь"):
        return 1
    if c.startswith("сумки"):
        return 4
    if c.startswith("головные") or c.startswith("аксессуар"):
        return 3
    # Костюмы, Old Money, Зима, Худи, Футболки, Куртки, Штаны, Шорты,
    # "Только в основной канал" и всё прочее -> Одежда
    return 2


# ============ ОПИСАНИЕ (из данных TG) ============
def _build_description(sizes: str, material: str) -> str:
    """
    Собирает описание как на скринах 1-2:
        🌿 Размеры: {sizes}
        🌿 {material}

        💎 Отзывы: {REVIEWS_URL}
        💎 Гарантии: {GUARANTEES_URL}
    Пустые/незначащие значения пропускаются.
    """
    lines = []

    s = (sizes or "").strip()
    if s and s not in ("—", "-", "Не важно"):
        lines.append(f"{EMOJI_LEAF} Размеры: {s}")

    m = (material or "").strip()
    if m and m not in ("Не указано", "—", "-"):
        lines.append(f"{EMOJI_LEAF} {m}")

    if lines:
        lines.append("")  # пустая строка-отступ перед ссылками

    lines.append(f"{EMOJI_GEM} Отзывы: {REVIEWS_URL}")
    lines.append(f"{EMOJI_GEM} Гарантии: {GUARANTEES_URL}")

    return "\n".join(lines)


# ============ ЗАГРУЗКА ФОТО НА IMGBB ============
async def _upload_via_pinterest(photo_bytes: bytes):
    """Переиспользуем загрузчик из pinterest_export (тот же ключ ImgBB)."""
    try:
        import pinterest_export as pe  # ленивый импорт -> без циклических зависимостей
        fn = getattr(pe, "upload_to_imgbb", None)
        if not fn:
            return None
        res = fn(photo_bytes)
        if asyncio.iscoroutine(res):
            res = await res
        return res or None
    except Exception as e:
        logger.warning(f"VK: reuse pe.upload_to_imgbb failed: {e}")
        return None


async def _upload_own(photo_bytes: bytes):
    """Запасной вариант: своя загрузка на ImgBB по ключу из окружения."""
    key = os.getenv("IMGBB_API_KEY") or os.getenv("IMGBB_KEY") or os.getenv("IMGBB_TOKEN")
    if not key:
        logger.warning("VK: нет IMGBB_API_KEY в окружении для запасной загрузки")
        return None
    try:
        import aiohttp  # локальный импорт: нужен только для запасной загрузки
        b64 = base64.b64encode(photo_bytes).decode()
        data = {"key": key, "image": b64}
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post("https://api.imgbb.com/1/upload", data=data) as r:
                if r.status != 200:
                    logger.warning(f"VK: ImgBB HTTP {r.status}")
                    return None
                j = await r.json()
                return (j.get("data") or {}).get("url")
    except Exception as e:
        logger.warning(f"VK: own ImgBB upload failed: {e}")
        return None


async def upload_to_imgbb(photo_bytes: bytes):
    """Сначала пробуем загрузчик Pinterest-модуля, потом свой запасной."""
    url = await _upload_via_pinterest(photo_bytes)
    if url:
        return url
    return await _upload_own(photo_bytes)


# ============ НАКОПИТЕЛЬ OFFER'ОВ ============
def _append_offer(offer: dict):
    with open(VK_STORE, "a", encoding="utf-8") as f:
        f.write(json.dumps(offer, ensure_ascii=False) + "\n")


def _read_offers() -> list:
    if not os.path.exists(VK_STORE):
        return []
    out = []
    with open(VK_STORE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("VK: битая строка в vk_batch.jsonl, пропущена")
    return out


def count_vk() -> int:
    return len(_read_offers())


VK_MAX_PICTURES = 5  # VK берёт до 5 фото на товар


# ============ ДОБАВЛЕНИЕ ТОВАРА ============
async def add_offer(photo_bytes_list, name, price, category, sizes, material, image_urls=None):
    """
    Добавляет offer в партию. Возвращает (added, total).
    В VK идут первые 5 фото -> несколько <picture>. Порядок сохраняется, первое фото первым.
    added=0, если ни одна картинка не загрузилась (offer без <picture> VK пропустит).
    image_urls можно передать готовым списком, чтобы не грузить фото повторно.
    """
    urls = []
    if image_urls:
        urls = [u for u in image_urls if u][:VK_MAX_PICTURES]
    elif photo_bytes_list:
        for b in photo_bytes_list[:VK_MAX_PICTURES]:
            u = await upload_to_imgbb(b)
            if u:
                urls.append(u)

    if not urls:
        return 0, count_vk()

    try:
        price_int = int(price) if price else 0
    except (TypeError, ValueError):
        price_int = 0

    offer = {
        "name": str(name or "Товар").strip(),
        "price": price_int,
        "category_id": _category_id(category),
        "pictures": urls,
        "description": _build_description(sizes, material),
    }
    _append_offer(offer)
    return 1, count_vk()


# ============ РЕНДЕР YML ============
def build_yml() -> str:
    offers = _read_offers()
    date = datetime.now().strftime("%Y-%m-%d %H:%M")

    p = []
    p.append('<?xml version="1.0" encoding="utf-8"?>')
    p.append(f'<yml_catalog date="{date}">')
    p.append('  <shop>')
    p.append(f'    <name>{_xml_escape(SHOP_NAME)}</name>')
    p.append(f'    <company>{_xml_escape(SHOP_NAME)}</company>')
    p.append(f'    <url>{_xml_escape(SHOP_URL)}</url>')
    p.append('    <currencies>')
    p.append('      <currency id="RUB" rate="1"/>')
    p.append('    </currencies>')
    p.append('    <categories>')
    for cid, cname in VK_CATEGORIES:
        p.append(f'      <category id="{cid}">{_xml_escape(cname)}</category>')
    p.append('    </categories>')
    p.append('    <offers>')
    for i, o in enumerate(offers, start=1):
        p.append(f'      <offer id="{i}" available="true">')
        p.append(f'        <price>{o.get("price", 0)}</price>')
        p.append('        <currencyId>RUB</currencyId>')
        p.append(f'        <categoryId>{o.get("category_id", 2)}</categoryId>')
        pics = o.get("pictures") or ([o["picture"]] if o.get("picture") else [])
        for pic in pics[:VK_MAX_PICTURES]:
            p.append(f'        <picture>{_xml_escape(pic)}</picture>')
        p.append(f'        <name>{_xml_escape(o.get("name", "Товар"))}</name>')
        desc = _xml_escape(o.get("description", ""))
        p.append(f'        <description>{desc}</description>')
        p.append('      </offer>')
    p.append('    </offers>')
    p.append('  </shop>')
    p.append('</yml_catalog>')
    return "\n".join(p)


def get_vk_path():
    """Рендерит vk_batch.xml из накопителя и возвращает путь. None если пусто.
    Пишем в UTF-8 с BOM ('utf-8-sig') — как блокнот Windows: VK так надёжнее распознаёт кодировку."""
    if not _read_offers():
        return None
    with open(VK_YML, "w", encoding="utf-8-sig") as f:
        f.write(build_yml())
    return VK_YML


def clear_vk():
    """Архивирует текущую партию. Возвращает имя архива или None если пусто."""
    if not os.path.exists(VK_STORE) or not _read_offers():
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archived = f"vk_batch_{stamp}.jsonl"
    os.rename(VK_STORE, archived)
    return archived
