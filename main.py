from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import Command
from aiohttp import web
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InputMediaPhoto, FSInputFile, TelegramObject
from aiogram.utils.keyboard import InlineKeyboardBuilder
import asyncio
import os
import re
import logging
from datetime import datetime, timedelta
from collections import deque
from html import escape as html_escape
from zoneinfo import ZoneInfo

import pinterest_export as pe
import vk_export as vke
from price_calc import (
    PriceCalculationError,
    calculate_price,
    format_rate,
    get_exchange_rate,
    parse_price_line,
    set_exchange_rate,
)

# ============ ОЧЕРЕДЬ ПУБЛИКАЦИЙ ============
post_queue: deque = deque()
last_published_at: datetime | None = None
MIN_INTERVAL = 5 * 60  # секунд между постами
MOSCOW_TZ = ZoneInfo("Europe/Moscow")
MATERIALS_NOTE = "Все материалы, бирки, фурнитура соответствуют. 1:1."

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============ ЗАЩИТА ============
ALLOWED_USERS = {240939473, 294396177}

class AllowedUsersMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = data.get("event_from_user")
        if user and user.id not in ALLOWED_USERS:
            return  # молча игнорируем чужих
        return await handler(event, data)

# ============ НАСТРОЙКИ ============
BOT_TOKEN = os.getenv("BOT_TOKEN")
MAIN_CHANNEL = os.getenv("MAIN_CHANNEL")
MOSCOW_GROUP = "@berishmotmoscow"  # группа московского склада

MAX_PHOTOS = 9        # бот принимает до 9 фото
VK_PHOTOS = 5         # в VK идут первые 5

CATEGORY_MAP = {
    "Обувь Adidas": ("Обувь Adidas", "@shoespremium1"),
    "Обувь Nike": ("Обувь Nike", "@shoesbuy1"),
    "Обувь New Balance": ("Обувь New Balance", "@shoesbuynb"),
    "Обувь Микс": ("Обувь Микс", "@shoesmix1"),
    "Костюмы": ("Все костюмы тут", "@kostumtut"),
    "Old Money": ("Все вещи Old Money", "@oldmoney_premium"),
    "Зима": ("Зимняя коллекция", "@wintercloth1"),
    "Худи и Свитшоты": ("Худи и свитшоты", "@hoodiespremium"),
    "Футболки и Рубашки": ("Футболки и рубашки", "@t_shirtsbuy"),
    "Куртки и Ветровки": ("Куртки и ветровки", "@jacketsbuy"),
    "Штаны и Джинсы": ("Штаны и джинсы", "@jeanspremium"),
    "Шорты и Трусы": ("Шорты и трусы", "@shortsbuy"),
    "Головные уборы и Шарфы": ("Головные уборы и шарфы", "@headdressbuy"),
    "Аксессуары": ("Все аксессуары", "@accessories_buy"),
    "Сумки и Кошельки": ("Сумки и кошельки", "@bags_wallets"),
}

# ============ СКЛАДЫ / МАРШРУТИЗАЦИЯ ============
# 3 кнопки. routing определяет, куда идёт товар.
WAREHOUSE_OPTIONS = ["Зарубежный склад", "Доставка 2-3 дня", "Склад Москва"]

# Текст, который выводится в посте в строке 🚀
WAREHOUSE_TEXT = {
    "Зарубежный склад": "Зарубежный склад",
    "Доставка 2-3 дня": "Доставка 2-3 дня",
    "Склад Москва": "Доставка 2-3 дня",   # <-- если для Москвы нужен другой текст в посте, поменяй здесь
}

def routing_for(warehouse: str) -> dict:
    """Куда публиковать/выгружать товар в зависимости от выбранного склада."""
    if warehouse == "Зарубежный склад":
        return {"main": True,  "sub": True,  "moscow": False, "vk": True, "pinterest": True}
    if warehouse == "Доставка 2-3 дня":
        return {"main": True,  "sub": True,  "moscow": True,  "vk": True, "pinterest": False}
    if warehouse == "Склад Москва":
        return {"main": False, "sub": False, "moscow": True,  "vk": True, "pinterest": False}
    # безопасный дефолт = как зарубежный склад
    return {"main": True, "sub": True, "moscow": False, "vk": True, "pinterest": True}

SHOE_KEYWORDS = ["кроссовки", "сникер", "ботинк", "туфл", "кед", "обувь", "EUR", "35-", "36-", "39-", "40-", "размер обуви"]

LATIN_TO_CYRILLIC = str.maketrans({
    "a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у",
    "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К",
    "M": "М", "O": "О", "P": "Р", "T": "Т", "X": "Х",
})

def visual_name(name: str) -> str:
    """Заменяет только латинские буквы с визуально идентичными кириллическими аналогами."""
    return (name or "Товар").translate(LATIN_TO_CYRILLIC)

def _telegram_url(channel: str) -> str:
    return f"https://t.me/{str(channel).lstrip('@')}"

def _forward_channel(category: str) -> str | None:
    if category == "Только в основной канал":
        return None
    return CATEGORY_MAP.get(category, ("Все товары тут", MAIN_CHANNEL))[1]

def _catalog_target(destination: str, category: str) -> tuple[str, str]:
    """Возвращает подпись и адрес перекрёстной ссылки для конкретного канала."""
    if destination == "main" and category in CATEGORY_MAP:
        label, channel = CATEGORY_MAP[category]
        return label, _telegram_url(channel)
    return "Все товары тут", _telegram_url(MAIN_CHANNEL)

def _build_post_text(name: str, new_price, old_price, material: str, sizes: str,
                     warehouse: str, category: str, destination: str) -> str:
    catalog_label, catalog_url = _catalog_target(destination, category)
    wh_text = WAREHOUSE_TEXT.get(warehouse, warehouse)
    safe_name = html_escape(visual_name(name))
    safe_sizes = html_escape(str(sizes))
    safe_new_price = html_escape(str(new_price))
    safe_old_price = html_escape(str(old_price))
    safe_material = html_escape(str(material))
    return (
        f"<b>{safe_name}</b>\n\n"
        f"📏 <b>Размеры:</b> {safe_sizes}\n\n"
        f"💸 <b>Цена:</b> {safe_new_price}₽ <s>{safe_old_price}₽</s>\n\n"
        f"🪴 <b>Материалы:</b> {safe_material}\n"
        f"{MATERIALS_NOTE}\n\n"
        f"🚀 {wh_text}\n\n"
        f"🔗 <a href='{catalog_url}'>{catalog_label}</a>\n\n"
        f"🫶 Бесплатный обмен/возврат\n\n"
        f"💖 <a href='https://t.me/berishmotru'>Отзывы</a> | "
        f"<a href='https://telegra.ph/Pochemu-mozhno-doveryat-Berishmot-Store-04-20-2'>Гарантии</a>\n\n"
        f"Для заказа: @viktor_zorin\n\n"
        f"👉 Полный каталог / Full catalog: "
        f"<a href='https://berishmot.store'>Berishmot.store</a>"
    )

# ============ FSM ============
class PostForm(StatesGroup):
    waiting_for_photos_and_text = State()
    waiting_for_shoe_size = State()
    waiting_for_category = State()
    waiting_for_warehouse = State()
    waiting_for_confirmation = State()

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
dp.message.middleware(AllowedUsersMiddleware())
dp.callback_query.middleware(AllowedUsersMiddleware())

# ============ КЛАВИАТУРЫ ============
def get_start_kb():
    return types.ReplyKeyboardMarkup(keyboard=[[types.KeyboardButton(text="/post")]], resize_keyboard=True)

def cancel_inline():
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data="cancel")
    return builder.as_markup()

# ============ ПАРСЕР ============
def parse_caption(text: str):
    if not text:
        return "Без названия", None, "Не указано", None
    lines = text.splitlines()
    name = lines[0].strip() if lines else "Без названия"
    price = None
    price_mode = None
    price_line_index = None
    # Цена всегда находится в первой непустой строке после названия.
    # Благодаря этому любые цифры в многострочном описании сохраняются
    # и не могут случайно стать ценой.
    first_content_index = next(
        (i for i, line in enumerate(lines[1:], start=1) if line.strip()),
        None,
    )
    if first_content_index is not None:
        parsed = parse_price_line(lines[first_content_index])
        if parsed.value is not None:
            price = parsed.value
            price_mode = parsed.mode
            price_line_index = first_content_index
        elif parsed.mode == "yuan":
            price_mode = "yuan"
            price_line_index = first_content_index

    if price_line_index is not None:
        # Всё после строки с ценой — единое многострочное описание.
        material = "\n".join(lines[price_line_index + 1:]).strip() or "Не указано"
    else:
        material = "Не указано"
    return name, price, material, price_mode

# ============ ХЕНДЛЕРЫ ============
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "🚀 <b>Berishmot Bot v2.2</b>\n\n"
        "📸 Пришли альбом + подпись в первом фото\n"
        "💱 Закупку указывай строкой из 1–3 цифр, например <b>235</b>\n"
        "⚙️ Курс: /kurs",
        parse_mode="HTML",
        reply_markup=get_start_kb(),
    )


@dp.message(Command("kurs"))
async def cmd_kurs(message: types.Message):
    """Показывает или меняет курс юаня для будущих товаров."""
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 1:
        await message.answer(f"💱 Текущий курс: 1 ¥ = {format_rate(get_exchange_rate())} ₽")
        return
    try:
        rate = set_exchange_rate(parts[1].strip())
    except PriceCalculationError as exc:
        await message.answer(f"❌ {exc}")
        return
    await message.answer(f"✅ Курс сохранён: 1 ¥ = {format_rate(rate)} ₽")

@dp.message(Command("post"))
async def cmd_post(message: types.Message, state: FSMContext):
    await state.clear()
    await state.set_state(PostForm.waiting_for_photos_and_text)
    await message.answer(
        f"📸 Пришли альбом (до {MAX_PHOTOS} фото)\n\n"
        "В подписи к первому фото:\n"
        "Название\n"
        "235\n"
        "Материалы / состав\n\n"
        "Бот сам посчитает цену в рублях после выбора категории.",
        reply_markup=cancel_inline(),
    )

@dp.message(PostForm.waiting_for_photos_and_text, F.photo)
async def process_photos_with_caption(message: types.Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get("photos", [])
    processed = data.get("caption_processed", False)
    reminded = data.get("caption_reminded", False)
    completion_notified = data.get("photos_completion_notified", False)

    photo_id = message.photo[-1].file_id
    if photo_id not in photos and len(photos) < MAX_PHOTOS:
        photos.append(photo_id)
        await state.update_data(photos=photos)

    if not processed and message.caption:
        name, price, material, price_mode = parse_caption(message.caption)
        if price is None:
            await message.answer(
                "❌ Не нашёл цену в подписи.\n\n"
                "Формат подписи к первому фото:\n"
                "<b>Название товара\n235\nМатериал / состав</b>\n\n"
                "1–3 цифры — закупка в юанях; 4–6 цифр — готовая цена в рублях.",
                parse_mode="HTML",
                reply_markup=cancel_inline()
            )
            return

        # Флаг выставляем только после успешной валидации
        await state.update_data(caption_processed=True)
        old_price = int(price * 1.3) if price_mode == "rubles" else None
        await state.update_data(
            name=name,
            source_price=price,
            price_mode=price_mode,
            new_price=price if price_mode == "rubles" else None,
            old_price=old_price,
            material=material,
        )

        full_text = (message.caption or "") + (name or "")
        is_shoe = any(kw.lower() in full_text.lower() for kw in SHOE_KEYWORDS)

        if is_shoe:
            await state.update_data(sizes="—")
            kb = InlineKeyboardBuilder()
            sizes_list = ["35–46", "36–46", "35–47", "36–41", "39–44", "40–45", "40–46", "41–46", "41–44", "42–45", "38–45", "37–44", "Не важно"]
            for size in sizes_list:
                kb.button(text=size, callback_data=f"shoe:{size}")
            kb.adjust(3)
            kb.row(types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"))
            await state.set_state(PostForm.waiting_for_shoe_size)
            await message.answer("👟 Обувь определена\n\nВыбери диапазон размеров:", reply_markup=kb.as_markup())
        else:
            await state.update_data(sizes="S–M–L–XL–2XL–3XL")
            await state.set_state(PostForm.waiting_for_category)
            await show_category_inline(message)

    elif not processed and not message.caption and not reminded:
        # Фото пришло без подписи — напоминаем один раз
        await state.update_data(caption_reminded=True)
        await message.answer(
            "📝 Фото получено!\n\n"
            "Теперь пришли альбом <b>с подписью</b> на первом фото:\n\n"
                "<b>Название товара\nЗакупка (например: 235)\nМатериал / состав</b>",
            parse_mode="HTML",
            reply_markup=cancel_inline()
        )

    if len(photos) >= MAX_PHOTOS and not completion_notified:
        await state.update_data(photos_completion_notified=True)
        await message.answer(f"✅ {MAX_PHOTOS} фото добавлено", reply_markup=cancel_inline())

async def show_category_inline(message: types.Message):
    builder = InlineKeyboardBuilder()
    for cat in CATEGORY_MAP.keys():
        builder.button(text=cat, callback_data=f"cat:{cat}")
    builder.button(text="🔸 Только в основной канал", callback_data="cat:main_only")
    builder.adjust(2)
    builder.row(types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"))
    await message.answer("Выбери категорию:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("shoe:"))
async def set_shoe_size_callback(callback: types.CallbackQuery, state: FSMContext):
    size = callback.data.split(":", 1)[1]
    await state.update_data(sizes=size)
    await callback.message.edit_text(f"✅ Размеры: {size}")
    await state.set_state(PostForm.waiting_for_category)
    await show_category_inline(callback.message)
    await callback.answer()

@dp.callback_query(F.data.startswith("cat:"))
async def choose_category_callback(callback: types.CallbackQuery, state: FSMContext):
    cat = callback.data.split(":", 1)[1]
    if cat == "main_only":
        cat = "Только в основной канал"

    data = await state.get_data()
    if data.get("price_mode") == "yuan":
        try:
            calculation = calculate_price(
                data.get("source_price"),
                cat,
                f"{data.get('name', '')}\n{data.get('material', '')}",
            )
        except PriceCalculationError as exc:
            await callback.answer()
            await callback.message.answer(f"❌ {exc}\n\nДобавь тип вещи в название и выбери категорию ещё раз.")
            return
        new_price = calculation.price
        await state.update_data(
            new_price=new_price,
            old_price=int(new_price * 1.3),
            calculator_category=calculation.category_label,
        )
        category_status = f"✅ Категория: {cat}\n💱 Цена рассчитана: {new_price} ₽"
    else:
        category_status = f"✅ Категория: {cat}"

    await state.update_data(category=cat)
    await callback.message.edit_text(category_status)
    builder = InlineKeyboardBuilder()
    for w in WAREHOUSE_OPTIONS:
        builder.button(text=w, callback_data=f"wh:{w}")
    builder.adjust(1)
    builder.row(types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"))
    await state.set_state(PostForm.waiting_for_warehouse)
    await callback.message.answer("Выбери склад:", reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data.startswith("wh:"))
async def choose_warehouse_callback(callback: types.CallbackQuery, state: FSMContext):
    warehouse = callback.data.split(":", 1)[1]
    await state.update_data(warehouse=warehouse)
    await callback.message.edit_text(f"✅ Склад: {warehouse}")
    await state.set_state(PostForm.waiting_for_confirmation)
    await send_preview(callback.message, state)
    await callback.answer()

async def send_preview(message: types.Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get("photos", [])
    name = data.get("name", "Товар")
    new_p = data.get("new_price", 0)
    old_p = data.get("old_price", 0)
    material = data.get("material", "Не указано")
    sizes = data.get("sizes", "—")
    warehouse = data.get("warehouse", "Зарубежный склад")
    category = data.get("category", "Только в основной канал")

    post_text = _build_post_text(
        name, new_p, old_p, material, sizes, warehouse, category, "main"
    )

    media = [InputMediaPhoto(media=photos[0], caption="🔍 Превью\n\n" + post_text, parse_mode="HTML")]
    for pid in photos[1:]:
        media.append(InputMediaPhoto(media=pid))

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Опубликовать", callback_data="confirm_post")
    builder.button(text="❌ Отмена", callback_data="cancel")
    await bot.send_media_group(chat_id=message.chat.id, media=media)
    await message.answer("Подтверди публикацию:", reply_markup=builder.as_markup())

async def _do_publish(photos: list, name: str, new_price, old_price, material: str,
                      sizes: str, warehouse: str, category: str,
                      forward_to: str | None, route: dict) -> list[str]:
    """Публикует пост с перекрёстной ссылкой, отдельной для каждого назначения."""

    published = []

    async def _send(chat_id, destination):
        post_text = _build_post_text(
            name, new_price, old_price, material, sizes, warehouse, category, destination
        )
        media = [InputMediaPhoto(media=photos[0], caption=post_text, parse_mode="HTML")]
        for pid in photos[1:]:
            media.append(InputMediaPhoto(media=pid))
        await bot.send_media_group(chat_id, media)
        published.append(chat_id)

    if route.get("main"):
        await _send(MAIN_CHANNEL, "main")
    if route.get("sub") and forward_to:
        await _send(forward_to, "subcategory")
    if route.get("moscow"):
        await _send(MOSCOW_GROUP, "moscow")

    return published


async def _queue_worker():
    """Воркер: публикует посты из очереди с интервалом ≥ MIN_INTERVAL секунд."""
    global last_published_at
    while True:
        await asyncio.sleep(10)
        if not post_queue:
            continue
        now = datetime.now()
        if last_published_at and (now - last_published_at).total_seconds() < MIN_INTERVAL:
            continue
        post = post_queue.popleft()
        chat_id = post["chat_id"]
        route = post["route"]
        try:
            published = await _do_publish(
                post["photos"], post["name"], post["price"], post["old_price"],
                post["material"], post["sizes"], post["warehouse"], post["category"],
                post["forward_to"], route
            )
            last_published_at = datetime.now()
            pub_str = ", ".join(published) if published else "—"
            await bot.send_message(chat_id, f"✅ Опубликовано: {pub_str}\nГотов к следующему!")
            asyncio.create_task(_add_to_catalogs(
                post["photos"], post["name"], post["category"],
                post.get("sizes", "—"), post.get("material", "Не указано"), post.get("price", 0),
                route.get("pinterest", False), route.get("vk", True)
            ))
            if post_queue:
                next_time = (datetime.now(MOSCOW_TZ) + timedelta(seconds=MIN_INTERVAL)).strftime("%H:%M")
                await bot.send_message(chat_id, f"⏳ Следующий в очереди выйдет в ~{next_time}")
        except Exception as e:
            await bot.send_message(chat_id, f"❌ Ошибка публикации из очереди: {e}")


@dp.callback_query(F.data == "confirm_post")
async def confirm_publish(callback: types.CallbackQuery, state: FSMContext):
    global last_published_at
    data = await state.get_data()
    photos = data.get("photos", [])
    name = data.get("name", "Товар")
    category = data.get("category", "Только в основной канал")
    sizes = data.get("sizes", "—")
    material = data.get("material", "Не указано")
    price = data.get("new_price", 0)
    old_price = data.get("old_price", 0)
    warehouse = data.get("warehouse", "Зарубежный склад")
    forward_to = _forward_channel(category)
    route = routing_for(warehouse)
    chat_id = callback.message.chat.id

    now = datetime.now()
    elapsed = (now - last_published_at).total_seconds() if last_published_at else MIN_INTERVAL
    can_now = elapsed >= MIN_INTERVAL and not post_queue

    if can_now:
        try:
            published = await _do_publish(
                photos, name, price, old_price, material, sizes, warehouse, category,
                forward_to, route
            )
            last_published_at = datetime.now()
            pub_str = ", ".join(published) if published else "—"
            await callback.message.edit_text(f"✅ Опубликовано в: {pub_str}")
            await callback.message.answer("Готов к следующему!", reply_markup=get_start_kb())
            asyncio.create_task(_add_to_catalogs(
                photos, name, category, sizes, material, price,
                route.get("pinterest", False), route.get("vk", True)
            ))
        except Exception as e:
            await callback.message.answer(f"❌ Ошибка: {str(e)}")
    else:
        post_queue.append({
            "photos": photos, "forward_to": forward_to, "name": name,
            "category": category, "chat_id": chat_id, "sizes": sizes,
            "material": material, "price": price, "old_price": old_price,
            "warehouse": warehouse, "route": route,
        })
        pos = len(post_queue)
        wait_secs = (MIN_INTERVAL - elapsed) + (pos - 1) * MIN_INTERVAL
        next_time = (datetime.now(MOSCOW_TZ) + timedelta(seconds=wait_secs)).strftime("%H:%M")
        await callback.message.edit_text(
            f"📋 Пост #{pos} поставлен в очередь\n⏳ Выйдет в ~{next_time}"
        )
        await callback.message.answer("Готов к следующему!", reply_markup=get_start_kb())

    await state.clear()
    await callback.answer()


async def _add_to_catalogs(photo_ids: list, name: str, category: str,
                           sizes: str = "—", material: str = "Не указано", price: int = 0,
                           to_pinterest: bool = True, to_vk: bool = True):
    """Фоновая задача: скачать фото один раз -> Pinterest (1 фото) и/или VK (до 5 фото)."""
    if not (to_pinterest or to_vk):
        return

    need = VK_PHOTOS if to_vk else max(1, pe.PINS_PER_PRODUCT)
    photo_bytes_list = []
    try:
        for file_id in photo_ids[:need]:
            file = await bot.get_file(file_id)
            buf = await bot.download_file(file.file_path)
            photo_bytes_list.append(buf.read())
    except Exception as e:
        logger.error(f"download photos error: {e}", exc_info=True)
        return

    # --- Pinterest (первое фото) ---
    if to_pinterest:
        try:
            added, total = await pe.add_product(
                photo_bytes_list[:pe.PINS_PER_PRODUCT],
                name,
                category,
                material,
            )
            logger.info(f"Pinterest batch: +{added} пин(а), итого {total}")
        except Exception as e:
            logger.error(f"Pinterest add_product error: {e}", exc_info=True)

    # --- VK YML (первые 5 фото) ---
    if to_vk:
        try:
            vk_added, vk_total = await vke.add_offer(
                photo_bytes_list[:VK_PHOTOS], visual_name(name), price, category, sizes, material
            )
            logger.info(f"VK batch: +{vk_added} offer(ов), итого {vk_total}")
        except Exception as e:
            logger.error(f"VK add_offer error: {e}", exc_info=True)

@dp.callback_query(F.data == "cancel")
async def cancel_handler(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("🚫 Отменено", reply_markup=get_start_kb())
    await callback.answer()

# ============ PINTEREST КОМАНДЫ ============
@dp.message(Command("statspinvk"))
async def cmd_stats_pinvk(message: types.Message):
    """Показывает текущий размер обеих экспортных партий по запросу."""
    await message.answer(
        f"📊 <b>Текущие партии</b>\n\n"
        f"📌 Pinterest: {pe.count_batch()} пин(а)\n"
        f"🟦 VK: {vke.count_vk()} offer(ов)",
        parse_mode="HTML"
    )

@dp.message(Command("pinterest"))
async def cmd_pinterest_count(message: types.Message):
    count = pe.count_batch()
    await message.answer(
        f"📌 <b>Pinterest партия:</b> {count} пин(а)\n\n"
        f"• /export — скачать CSV\n"
        f"• /clear_batch — архивировать и начать новую",
        parse_mode="HTML"
    )

@dp.message(Command("export"))
async def cmd_export(message: types.Message):
    path = None
    try:
        path = await asyncio.to_thread(pe.get_csv_path)
        count = await asyncio.to_thread(pe.count_batch) if path else 0
    except Exception:
        logger.exception("Pinterest export storage error")
        if path and os.path.basename(path).startswith("pinterest_export_"):
            os.unlink(path)
        await message.answer(
            "❌ Экспорт Pinterest недоступен: не удалось прочитать App Storage. "
            "Проверьте, что в проекте создан и подключён бакет по умолчанию."
        )
        return
    try:
        if not path:
            await message.answer("📭 Партия пустая — публикуй товары, они добавятся автоматически.")
            return
        abs_path = os.path.abspath(path)
        if not os.path.exists(abs_path):
            await message.answer(f"⚠️ Файл не найден: {abs_path}")
            return
        doc = FSInputFile(abs_path, filename="pinterest_batch.csv")
        await message.answer_document(
            doc,
            caption=f"📋 Pinterest CSV — {count} пин(а)\n\nЗагрузи в Pinterest: Настройки → Импорт контента"
        )
    except Exception:
        logger.exception("Pinterest export Telegram send error")
        try:
            await message.answer("❌ Не удалось отправить экспорт Pinterest в Telegram. Повторите попытку.")
        except Exception:
            logger.exception("Failed to report Pinterest export send error")
    finally:
        if path and os.path.basename(path).startswith("pinterest_export_"):
            os.unlink(path)

@dp.message(Command("clear_batch"))
async def cmd_clear_batch(message: types.Message):
    try:
        archived = await asyncio.to_thread(pe.clear_batch)
    except Exception:
        logger.exception("Pinterest batch archive failed")
        await message.answer("❌ Не удалось очистить партию: проверьте App Storage и повторите команду.")
        return
    if not archived:
        await message.answer("📭 Нечего архивировать — партия и так пустая.")
        return
    await message.answer(f"✅ Партия архивирована как <code>{archived}</code>. Новая партия начата.", parse_mode="HTML")

# ============ VK КОМАНДЫ ============
@dp.message(Command("vk"))
async def cmd_vk_link(message: types.Message):
    """Показывает ссылку на XML-фид для вставки в VK + количество товаров."""
    count = vke.count_vk()
    base = os.getenv("PUBLIC_URL", "").rstrip("/")
    if base:
        link = f"{base}/vk_batch.xml"
    else:
        link = "задай PUBLIC_URL в Secrets (домен деплоя Replit), затем /vk_batch.xml"
    await message.answer(
        f"🟦 <b>VK фид:</b> {count} offer(ов)\n\n"
        f"Ссылка для VK (Товары → Добавить товары → поле «ссылка на файл»):\n"
        f"<code>{link}</code>\n\n"
        f"VK перечитывает фид сам каждые ~12 часов.\n\n"
        f"• /export_vk — скачать XML файлом\n"
        f"• /count_vk — сколько товаров\n"
        f"• /clear_vk — архивировать и начать новую партию",
        parse_mode="HTML"
    )

@dp.message(Command("count_vk"))
async def cmd_count_vk(message: types.Message):
    count = vke.count_vk()
    await message.answer(
        f"🟦 <b>VK партия:</b> {count} offer(ов)\n\n"
        f"• /vk — ссылка на фид\n"
        f"• /export_vk — скачать XML\n"
        f"• /clear_vk — архивировать и начать новую",
        parse_mode="HTML"
    )

@dp.message(Command("export_vk"))
async def cmd_export_vk(message: types.Message):
    try:
        path = await asyncio.to_thread(vke.get_vk_path)
        count = await asyncio.to_thread(vke.count_vk) if path else 0
    except Exception:
        logger.exception("VK export storage error")
        await message.answer(
            "❌ Экспорт VK недоступен: не удалось прочитать App Storage. "
            "Проверьте, что в проекте создан и подключён бакет по умолчанию."
        )
        return
    try:
        if not path:
            await message.answer("📭 VK партия пустая — публикуй товары, они добавятся автоматически.")
            return
        abs_path = os.path.abspath(path)
        if not os.path.exists(abs_path):
            await message.answer(f"⚠️ Файл не найден: {abs_path}")
            return
        doc = FSInputFile(abs_path, filename="vk_batch.xml")
        await message.answer_document(
            doc,
            caption=f"🟦 VK файл — {count} offer(ов)\n\nГрузи как есть: Товары → Добавить → Из файла"
        )
    except Exception:
        logger.exception("VK export Telegram send error")
        try:
            await message.answer("❌ Не удалось отправить экспорт VK в Telegram. Повторите попытку.")
        except Exception:
            logger.exception("Failed to report VK export send error")

@dp.message(Command("clear_vk"))
async def cmd_clear_vk(message: types.Message):
    try:
        archived = await asyncio.to_thread(vke.clear_vk)
    except Exception:
        logger.exception("VK batch archive failed")
        await message.answer("❌ Не удалось очистить VK-партию: проверьте App Storage и повторите команду.")
        return
    if not archived:
        await message.answer("📭 Нечего архивировать — VK партия и так пустая.")
        return
    try:
        await vke.cleanup_old_vk_images()
    except Exception:
        logger.exception("VK image cleanup failed after archiving")
        await message.answer(
            f"✅ VK партия архивирована как <code>{archived}</code>. "
            "⚠️ Очистка старых фото не удалась, повторите её при следующем /clear_vk.",
            parse_mode="HTML",
        )
        return
    await message.answer(f"✅ VK партия архивирована как <code>{archived}</code>. Новая партия начата.", parse_mode="HTML")

# ============ ВЕБ-СЕРВЕР (раздаёт XML по ссылке для VK) ============
async def _serve_vk_image(request):
    name = request.match_info["name"]
    if not re.fullmatch(r"[0-9a-fA-F]{32}\.jpeg", name):
        raise web.HTTPNotFound()
    image = await vke.get_vk_image(name)
    if image is None:
        raise web.HTTPNotFound()
    return web.Response(body=image, content_type="image/jpeg")


async def _serve_vk_yml(request):
    """VK читает этот адрес по ссылке. Всегда отдаём свежий XML (UTF-8 с BOM, как блокнот)."""
    try:
        yml = await asyncio.to_thread(vke.build_yml)
    except Exception as e:
        logger.error(f"serve vk yml error: {e}", exc_info=True)
        return web.Response(text="internal error", status=500)
    body = yml.encode("utf-8-sig")
    return web.Response(body=body, content_type="application/xml", charset="utf-8")

async def _index(request):
    return web.Response(
        text="Berishmot bot alive. Feed: /vk_batch.xml",
        content_type="text/plain", charset="utf-8"
    )

async def start_web():
    app = web.Application()
    app.router.add_get("/", _index)
    app.router.add_get("/vk_batch.xml", _serve_vk_yml)
    app.router.add_get("/vk_batch.yml", _serve_vk_yml)
    app.router.add_get("/img/{name}", _serve_vk_image)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "8080"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"🌐 Веб-сервер запущен на :{port} — фид доступен по /vk_batch.xml")

# ============ ЗАПУСК ============
async def main():
    logger.info("🚀 Berishmot Bot v2.2 запущен")
    await start_web()
    asyncio.create_task(_queue_worker())
    if os.getenv("DISABLE_BOT_POLLING") == "1":
        logger.info("Telegram polling disabled in development; web preview only")
        await asyncio.Event().wait()
        return
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
