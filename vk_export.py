import os
import json
import asyncio
import logging
import re
import secrets
import time
from datetime import datetime
from uuid import uuid4
from xml.sax.saxutils import escape as _xml_escape

from local_object_storage import Client, ObjectNotFoundError
import export_batch_storage as batch_storage
from price_calc import LEATHER_OUTERWEAR_TRIGGERS

logger = logging.getLogger(__name__)

# ============ НАСТРОЙКИ (правь тут) ============
SHOP_NAME = "Berishmot Store"
MANAGER_URL = "https://vk.com/berishmotvk"
SHOP_URL = MANAGER_URL      # <shop><url>
PRODUCT_URL = MANAGER_URL   # <offer><url> — куда ведёт карточка

# Ссылки как на скринах 1-2
REVIEWS_URL = "https://vk.com/topic-147969195_35462100?post=7351"
GUARANTEES_URL = "https://vk.com/topic-147969195_35466647"

# Эмодзи-стикеры как на скринах
EMOJI_LEAF = "🌿"   # размеры / материал
EMOJI_GEM = "💎"    # отзывы / гарантии
MATERIALS_NOTE = "Все материалы, бирки, фурнитура соответствуют. 1:1."

# Файлы партии
VK_STORE = "vk_batch.jsonl"   # накопитель offer'ов (по строке JSON на товар)
VK_YML = "vk_batch.xml"       # готовый файл для импорта в VK (YML внутри, расширение .xml)
PUBLIC_BASE = (os.getenv("PUBLIC_URL") or "https://berishmot.replit.app").rstrip("/")
VK_IMAGE_PREFIX = "vk_images/"
VK_IMAGE_TIME_PREFIX = "vk_image_times/"
_storage = Client()

# Категории VK (шапка YML)
VK_CATEGORIES = [
    (30000, "Гардероб"),
    (30005, "Old Money"),
    (30006, "Обувь"),
    (30007, "Аксессуары и сумки"),
    (40076, "Штаны и джинсы"),
    (40085, "Костюмы"),
    (40086, "Худи и свитшоты"),
    (40087, "Футболки и рубашки"),
    (40088, "Шорты и трусы"),
    (40092, "Кроссовки"),
    (40102, "Головные уборы и шарфы"),
    (50076, "Кожаная и замшевая верхняя одежда"),
    (50078, "Куртки и ветровки"),
    (50082, "Пуховики и зимние куртки"),
]


# ============ КАТЕГОРИИ: бот -> VK categoryId ============
VK_CATEGORY_IDS = {
    "Обувь Adidas": 40092,
    "Обувь Nike": 40092,
    "Обувь New Balance": 40092,
    "Обувь Микс": 30006,
    "Костюмы": 40085,
    "Old Money": 30005,
    "Зима": 50082,
    "Худи и Свитшоты": 40086,
    "Футболки и Рубашки": 40087,
    "Куртки и Ветровки": 50078,
    "Штаны и Джинсы": 40076,
    "Шорты и Трусы": 40088,
    "Головные уборы и Шарфы": 40102,
    "Аксессуары": 30007,
    "Сумки и Кошельки": 30007,
}


def _category_id(bot_category: str, name: str = "", material: str = "") -> int:
    """Возвращает VK categoryId для категории и признаков верхней одежды."""
    category = (bot_category or "").strip()
    if category in ("Зима", "Куртки и Ветровки"):
        text = f"{name or ''} {material or ''}".lower().replace("ё", "е")
        if "пуховик" in text:
            return 50082
        if any(trigger in text for trigger in LEATHER_OUTERWEAR_TRIGGERS):
            return 50076
    return VK_CATEGORY_IDS.get(category, 30000)


# ============ ОПИСАНИЕ (из данных TG) ============
def _build_description(sizes: str, material: str) -> str:
    """
    Собирает описание как на скринах 1-2:
        🌿 Размеры: {sizes}
        🌿 {material}
        Для оформления заказа пишите: {MANAGER_URL}

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

    lines.append(MATERIALS_NOTE)
    lines.append(f"Для оформления заказа пишите: {MANAGER_URL}")
    lines.append("")  # пустая строка-отступ перед ссылками

    lines.append(f"{EMOJI_GEM} Отзывы: {REVIEWS_URL}")
    lines.append(f"{EMOJI_GEM} Гарантии: {GUARANTEES_URL}")

    return "\n".join(lines)


# ============ ФОТО В APP STORAGE ============
async def get_vk_image(name: str) -> bytes | None:
    try:
        return await asyncio.to_thread(_storage.download_as_bytes, VK_IMAGE_PREFIX + name)
    except ObjectNotFoundError:
        return None


# ============ НАКОПИТЕЛЬ OFFER'ОВ ============
def _append_offer(offer: dict):
    batch_storage.append("vk", [offer])


def _read_legacy_offers() -> list:
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


def _read_offers() -> list:
    return batch_storage.rows("vk", _read_legacy_offers())


def count_vk() -> int:
    return len(_read_offers())


VK_MAX_PICTURES = 5  # VK берёт до 5 фото на товар


def _new_offer_id() -> str:
    return f"bm{datetime.now():%Y%m%d%H%M%S}-{secrets.randbelow(1000):03d}"


# ============ ДОБАВЛЕНИЕ ТОВАРА ============
async def add_offer(photo_bytes_list, name, price, category, sizes, material, image_urls=None):
    """
    Добавляет offer в партию. Возвращает (added, total).
    В VK идут первые 5 фото -> несколько <picture>. Порядок сохраняется, первое фото первым.
    added=0, если фотографий нет. image_urls оставлен для совместимости вызовов.
    """
    urls = []
    if not photo_bytes_list:
        return 0, count_vk()

    saved = []
    try:
        for photo in photo_bytes_list[:VK_MAX_PICTURES]:
            filename = f"{uuid4().hex}.jpeg"
            image_key = VK_IMAGE_PREFIX + filename
            await asyncio.to_thread(_storage.upload_from_bytes, image_key, photo)
            saved.append(image_key)
            marker = f"{VK_IMAGE_TIME_PREFIX}{int(time.time())}-{filename}"
            await asyncio.to_thread(_storage.upload_from_bytes, marker, b"")
            saved.append(marker)
            urls.append(f"{PUBLIC_BASE}/img/{filename}")
    except Exception:
        for key in saved:
            await asyncio.to_thread(_storage.delete, key, ignore_not_found=True)
        raise

    try:
        price_int = int(price) if price else 0
    except (TypeError, ValueError):
        price_int = 0

    existing_ids = {str(o.get("offer_id")) for o in _read_offers()}
    offer_id = _new_offer_id()
    while offer_id in existing_ids:
        offer_id = _new_offer_id()
    offer = {
        "offer_id": offer_id,
        "name": str(name or "Товар").strip(),
        "price": price_int,
        "category_id": _category_id(category, name, material),
        "pictures": urls,
        "description": _build_description(sizes, material),
    }
    try:
        _append_offer(offer)
    except Exception:
        for key in saved:
            await asyncio.to_thread(_storage.delete, key, ignore_not_found=True)
        raise
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
    for o in offers:
        offer_id = o.get("offer_id") or _new_offer_id()
        p.append(f'      <offer id="{_xml_escape(str(offer_id))}" available="true">')
        p.append(f'        <price>{o.get("price", 0)}</price>')
        p.append('        <currencyId>RUB</currencyId>')
        category_id = o.get("category_id", 30000)
        # Старые записи с локальными ID 1–4 не содержат исходную категорию бота.
        if category_id not in {cid for cid, _ in VK_CATEGORIES}:
            category_id = 30000
        p.append(f'        <categoryId>{category_id}</categoryId>')
        p.append('        <quantity>10</quantity>')
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
    offers = _read_offers()
    if not offers:
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archived = f"vk_batch_{stamp}_{uuid4().hex[:8]}.jsonl"
    with open(archived, "w", encoding="utf-8") as f:
        for offer in offers:
            f.write(json.dumps(offer, ensure_ascii=False) + "\n")
    batch_storage.archive_and_clear("vk", archived, offers)
    return archived


async def cleanup_old_vk_images() -> int:
    """Удаляет VK-фотографии, загруженные более 24 часов назад."""
    cutoff = time.time() - 24 * 60 * 60
    removed = 0
    markers = await asyncio.to_thread(_storage.list, prefix=VK_IMAGE_TIME_PREFIX)
    for marker in markers:
        match = re.fullmatch(
            rf"{VK_IMAGE_TIME_PREFIX}(\d+)-([0-9a-f]{{32}}\.jpeg)", marker.name
        )
        if match and int(match.group(1)) < cutoff:
            await asyncio.to_thread(
                _storage.delete, VK_IMAGE_PREFIX + match.group(2),
                ignore_not_found=True,
            )
            await asyncio.to_thread(_storage.delete, marker.name, ignore_not_found=True)
            removed += 1
    return removed
