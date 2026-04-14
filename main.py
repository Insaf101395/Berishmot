from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InputMediaPhoto
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web
import asyncio
import os
import re
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============ НАСТРОЙКИ ============
BOT_TOKEN = os.getenv("BOT_TOKEN")
MAIN_CHANNEL = os.getenv("MAIN_CHANNEL")
MOSCOW_GROUP = "@berishmotmoscow"

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

WAREHOUSE_OPTIONS = ["Склад Москва", "Доставка 2-5 дней", "Зарубежный склад"]

SHOE_KEYWORDS = ["кроссовки", "сникер", "ботинк", "туфл", "кед", "обувь", "EUR", "35-", "36-", "39-", "40-", "размер обуви"]

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

# ============ KEEP-ALIVE СЕРВЕР ============
async def keep_alive(request):
    return web.Response(text="Bot is alive!")

async def start_keep_alive():
    app = web.Application()
    app.router.add_get('/', keep_alive)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 5000)
    await site.start()
    logger.info("✅ Keep-Alive сервер запущен на порту 5000")

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
        return None, None, None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    name = lines[0] if lines else "Без названия"
    price = None
    material = "Не указано"
    for line in lines:
        price_match = re.search(r"(\d{4,6})", line)
        if price_match and price is None:
            price = int(price_match.group(1))
            continue
        if any(word in line.lower() for word in ["материал", "состав", "ткань", "%", "хлопок", "полиэстер", "эластан", "водонепроницаем"]):
            material = line
            continue
    if price is None and len(lines) > 1:
        try:
            price = int(re.search(r"\d+", lines[1]).group())
        except:
            pass
    return name, price, material

# ============ ХЕНДЛЕРЫ ============
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "🚀 <b>Berishmot Bot v2.1</b>\n\n"
        "📸 Пришли альбом + подпись в первом фото",
        parse_mode="HTML",
        reply_markup=get_start_kb(),
    )

@dp.message(Command("post"))
async def cmd_post(message: types.Message, state: FSMContext):
    await state.clear()
    await state.set_state(PostForm.waiting_for_photos_and_text)
    await message.answer(
        "📸 Пришли альбом (до 10 фото)\n\n"
        "В подписи к первому фото:\n"
        "Название\n"
        "Цена\n"
        "Материалы / состав",
        reply_markup=cancel_inline(),
    )

@dp.message(PostForm.waiting_for_photos_and_text, F.photo)
async def process_photos_with_caption(message: types.Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get("photos", [])
    processed = data.get("caption_processed", False)

    photo_id = message.photo[-1].file_id
    if photo_id not in photos:
        photos.append(photo_id)
        await state.update_data(photos=photos)

    if not processed and message.caption:
        await state.update_data(caption_processed=True)
        name, price, material = parse_caption(message.caption)

        if not price:
            await message.answer("❌ Не нашёл цену. Попробуй снова.", reply_markup=cancel_inline())
            return

        old_price = int(price * 1.3)
        await state.update_data(name=name, new_price=price, old_price=old_price, material=material)

        full_text = (message.caption or "") + (name or "")
        is_shoe = any(kw.lower() in full_text.lower() for kw in SHOE_KEYWORDS)

        if is_shoe:
            await state.update_data(sizes="—")
            kb = InlineKeyboardBuilder()
            sizes_list = [
                "35–46", "36–46", "35–47", "36–41", "39–44", "40–45",
                "40–46", "41–46", "41–44", "42–45", "38–45", "37–44", "Не важно"
            ]
            for size in sizes_list:
                kb.button(text=size, callback_data=f"shoe:{size}")
            kb.adjust(3)
            kb.row(types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"))
            await state.set_state(PostForm.waiting_for_shoe_size)
            await message.answer("👟 Обувь определена\n\nВыбери диапазон размеров (или «Не важно»):", reply_markup=kb.as_markup())
        else:
            await state.update_data(sizes="S–M–L–XL–2XL–3XL")
            await state.set_state(PostForm.waiting_for_category)
            await show_category_inline(message)

    if len(photos) >= 10:
        await message.answer("✅ 10 фото добавлено", reply_markup=cancel_inline())

async def show_category_inline(message: types.Message):
    builder = InlineKeyboardBuilder()
    for cat in CATEGORY_MAP.keys():
        builder.button(text=cat, callback_data=f"cat:{cat}")
    builder.button(text="🔸 Только в основной канал", callback_data="cat:main_only")
    builder.adjust(2)
    builder.row(types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"))
    await message.answer("Выбери категорию:", reply_markup=builder.as_markup())

# Callback-хендлеры
@dp.callback_query(F.data.startswith("shoe:"))
async def set_shoe_size_callback(callback: types.CallbackQuery, state: FSMContext):
    size = callback.data.split(":", 1)[1]
    await state.update_data(sizes=size)
    await callback.message.edit_text(f"✅ Размеры обуви: {size}")
    await state.set_state(PostForm.waiting_for_category)
    await show_category_inline(callback.message)
    await callback.answer()

@dp.callback_query(F.data.startswith("cat:"))
async def choose_category_callback(callback: types.CallbackQuery, state: FSMContext):
    cat = callback.data.split(":", 1)[1]
    if cat == "main_only":
        cat = "Только в основной канал"
    await state.update_data(category=cat)
    await callback.message.edit_text(f"✅ Категория: {cat}")
    builder = InlineKeyboardBuilder()
    for w in WAREHOUSE_OPTIONS:
        builder.button(text=w, callback_data=f"wh:{w}")
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
    warehouse = data.get("warehouse", "Склад Москва")

    post_text = (
        f"<b>{name}</b>\n\n"
        f"📏 <b>Размеры:</b> {sizes}\n\n"
        f"💸 <b>Цена:</b> {new_p}₽ <s>{old_p}₽</s>\n\n"
        f"🪴 <b>Материалы:</b> {material}\n\n"
        f"🚀 {warehouse}\n\n"
        f"🔗 <a href='https://t.me/{MOSCOW_GROUP.lstrip('@')}'>Склад Москва</a>\n\n"
        f"🫶 Бесплатный обмен/возврат\n\n"
        f"💖 <a href='https://t.me/berishmotru'>Отзывы</a> | "
        f"<a href='https://telegra.ph/Pochemu-mozhno-doveryat-Berishmot-Store-04-20-2'>Гарантии</a>\n\n"
        f"Для заказа: @viktor_zorin"
    )

    await state.update_data(preview_text=post_text)

    media = [InputMediaPhoto(media=photos[0], caption="🔍 Превью (новый стиль)\n\n" + post_text, parse_mode="HTML")]
    for pid in photos[1:]:
        media.append(InputMediaPhoto(media=pid))

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Опубликовать", callback_data="confirm_post")
    builder.button(text="❌ Отмена", callback_data="cancel")
    await bot.send_media_group(chat_id=message.chat.id, media=media)
    await message.answer("Подтверди публикацию:", reply_markup=builder.as_markup())

@dp.callback_query(F.data == "confirm_post")
async def confirm_publish(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    photos = data.get("photos", [])
    post_text = data.get("preview_text", "")

    media = [InputMediaPhoto(media=photos[0], caption=post_text, parse_mode="HTML")]
    for pid in photos[1:]:
        media.append(InputMediaPhoto(media=pid))

    try:
        await bot.send_media_group(MOSCOW_GROUP, media)
        await callback.message.edit_text(f"✅ Опубликовано в Московский склад")
        await callback.message.answer("Готов к следующему!", reply_markup=get_start_kb())
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {str(e)}")

    await state.clear()
    await callback.answer()

@dp.callback_query(F.data == "cancel")
async def cancel_handler(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("🚫 Отменено", reply_markup=get_start_kb())
    await callback.answer()

# ============ ЗАПУСК ============
async def main():
    asyncio.create_task(start_keep_alive())   # Запускаем keep-alive
    logger.info("🚀 Berishmot Bot запущен с Keep-Alive")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())