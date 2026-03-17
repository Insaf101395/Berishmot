      from aiogram import Bot, Dispatcher, types, F
      from aiogram.filters import Command
      from aiogram.fsm.context import FSMContext
      from aiogram.fsm.state import State, StatesGroup
      from aiogram.fsm.storage.memory import MemoryStorage
      from aiogram.types import ReplyKeyboardRemove, InputMediaPhoto
      from aiogram.utils.keyboard import InlineKeyboardBuilder
      import asyncio
      import os
      import re

      # ============ НАСТРОЙКИ ============
      BOT_TOKEN = os.getenv("BOT_TOKEN")
      MAIN_CHANNEL = os.getenv("MAIN_CHANNEL")          # @berishmot
      MOSCOW_GROUP = "@berishmotmoscow"

      CATEGORY_MAP = {
          "Обувь Adidas":          ("Обувь Adidas", "@shoespremium1"),
          "Обувь Nike":            ("Обувь Nike", "@shoesbuy1"),
          "Обувь New Balance":     ("Обувь New Balance", "@shoesbuynb"),
          "Обувь Микс":            ("Обувь Микс", "@shoesmix1"),
          "Костюмы":               ("Все костюмы тут", "@kostumtut"),
          "Old Money":             ("Все вещи Old Money", "@oldmoney_premium"),
          "Зима":                  ("Зимняя коллекция", "@wintercloth1"),
          "Худи и Свитшоты":       ("Худи и свитшоты", "@hoodiespremium"),
          "Футболки и Рубашки":    ("Футболки и рубашки", "@t_shirtsbuy"),
          "Куртки и Ветровки":     ("Куртки и ветровки", "@jacketsbuy"),
          "Штаны и Джинсы":        ("Штаны и джинсы", "@jeanspremium"),
          "Шорты и Трусы":         ("Шорты и трусы", "@shortsbuy"),
          "Головные уборы и Шарфы":("Головные уборы и шарфы", "@headdressbuy"),
          "Аксессуары":            ("Все аксессуары", "@accessories_buy"),
          "Сумки и Кошельки":      ("Сумки и кошельки", "@bags_wallets"),
      }

      WAREHOUSE_OPTIONS = [
          "Склад Москва",
          "Доставка 2-5 дней",
          "Зарубежный склад"
      ]

      SHOE_KEYWORDS = ["кроссовки", "сникер", "ботинк", "туфл", "кед", "обувь", "EUR", "35-", "36-", "39-", "40-", "размер обуви"]

      # ============ FSM ============
      class PostForm(StatesGroup):
          waiting_for_photos_and_text = State()
          waiting_for_shoe_size       = State()
          waiting_for_category        = State()
          waiting_for_warehouse       = State()
          waiting_for_confirmation    = State()

      bot = Bot(token=BOT_TOKEN)
      storage = MemoryStorage()
      dp = Dispatcher(storage=storage)

      def get_start_kb():
          return types.ReplyKeyboardMarkup(
              keyboard=[[types.KeyboardButton(text="/post")]],
              resize_keyboard=True
          )

      def cancel_inline():
          builder = InlineKeyboardBuilder()
          builder.button(text="❌ Отмена", callback_data="cancel")
          return builder.as_markup()

      def parse_caption(text: str):
          if not text:
              return None, None, None
          lines = [line.strip() for line in text.splitlines() if line.strip()]

          name = lines[0] if lines else "Без названия"
          price = None
          material = "Не указано"

          for line in lines:
              price_match = re.search(r'(\d{4,6})', line)
              if price_match and price is None:
                  price = int(price_match.group(1))
                  continue
              if any(word in line.lower() for word in ["материал", "состав", "ткань", "%", "хлопок", "полиэстер", "эластан", "водонепроницаем"]):
                  material = line
                  continue

          if price is None and len(lines) > 1:
              try:
                  price = int(re.search(r'\d+', lines[1]).group())
              except:
                  pass

          return name, price, material

      @dp.message(Command("start"))
      async def cmd_start(message: types.Message):
          await message.answer(
              "🚀 <b>Berishmot Bot v2.1</b> — обновлённый дизайн описания\n\n"
              "📸 Пришли альбом + подпись в первом фото",
              parse_mode="HTML",
              reply_markup=get_start_kb()
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
              "Материалы / состав\n\n"
              "Пример:\n"
              "Ветровка Kailas Pro\n"
              "7990\n"
              "Водонепроницаемая плащевая ткань, все детали 1:1 оригинал",
              reply_markup=cancel_inline()
          )

      @dp.message(PostForm.waiting_for_photos_and_text, F.photo)
      async def process_photos_with_caption(message: types.Message, state: FSMContext):
          data = await state.get_data()
          photos = data.get("photos", [])
          processed = data.get("caption_processed", False)

          # Проверяем лимит ПЕРЕД добавлением
          if len(photos) >= 10:
              await message.answer(
                  "⚠️ Максимум 10 фото в альбоме\n"
                  "Отправьте /post чтобы начать заново",
                  reply_markup=cancel_inline()
              )
              return

          photo_id = message.photo[-1].file_id
          if photo_id not in photos:
              photos.append(photo_id)
              await state.update_data(photos=photos)

          if not processed and message.caption:
              await state.update_data(caption_processed=True)
              name, price, material = parse_caption(message.caption)

              if not price:
                  await message.answer("❌ Не нашёл цену в подписи. Попробуй снова.", reply_markup=cancel_inline())
                  return

              old_price = int(price * 1.3)
              await state.update_data(name=name, new_price=price, old_price=old_price, material=material)

              full_text = (message.caption or "") + (name or "")
              is_shoe = any(kw.lower() in full_text.lower() for kw in SHOE_KEYWORDS)

              if is_shoe:
                  await state.update_data(sizes="—")
                  kb = InlineKeyboardBuilder()
                  for size in ["35–46 EUR", "36–41 EUR", "39–44 EUR", "40–45 EUR", "40–46 EUR"]:
                      kb.button(text=size, callback_data=f"shoe:{size}")
                  kb.button(text="❌ Отмена", callback_data="cancel")
                  await state.set_state(PostForm.waiting_for_shoe_size)
                  await message.answer("👟 Определили как обувь\nВыбери размеры:", reply_markup=kb.as_markup())
              else:
                  await state.update_data(sizes="S–M–L–XL–2XL–3XL")
                  await state.set_state(PostForm.waiting_for_category)
                  await show_category_inline(message)

          if len(photos) >= 10:
              await message.answer("✅ 10 фото добавлено. Отправьте /confirm для просмотра", reply_markup=cancel_inline())

      async def show_category_inline(message: types.Message):
          builder = InlineKeyboardBuilder()
          for cat in CATEGORY_MAP.keys():
              builder.button(text=cat, callback_data=f"cat:{cat}")
          builder.button(text="🔸 Только в основной канал", callback_data="cat:main_only")
          builder.adjust(2)
          builder.row(types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"))
          await message.answer("Выбери категорию:", reply_markup=builder.as_markup())

      @dp.callback_query(F.data.startswith("cat:"))
      async def process_category(callback: types.CallbackQuery, state: FSMContext):
          category = callback.data.split(":")[1]
          await state.update_data(category=category)

          if category == "main_only":
              await state.set_state(PostForm.waiting_for_confirmation)
              await send_preview(callback.message, state)
          else:
              await state.set_state(PostForm.waiting_for_warehouse)
              builder = InlineKeyboardBuilder()
              for wh in WAREHOUSE_OPTIONS:
                  builder.button(text=wh, callback_data=f"wh:{wh}")
              builder.row(types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"))
              await callback.message.answer("Выберите тип доставки:", reply_markup=builder.as_markup())

      @dp.callback_query(F.data.startswith("shoe:"))
      async def process_shoe_size(callback: types.CallbackQuery, state: FSMContext):
          size = callback.data.split(":")[1]
          await state.update_data(sizes=size)
          await state.set_state(PostForm.waiting_for_category)
          await callback.message.answer(f"✅ Размеры: {size}")
          await show_category_inline(callback.message)

      @dp.callback_query(F.data.startswith("wh:"))
      async def process_warehouse(callback: types.CallbackQuery, state: FSMContext):
          warehouse = callback.data.split(":")[1]
          await state.update_data(warehouse=warehouse)
          await state.set_state(PostForm.waiting_for_confirmation)
          await send_preview(callback.message, state)

      @dp.callback_query(F.data == "cancel")
      async def cancel_handler(callback: types.CallbackQuery, state: FSMContext):
          await state.clear()
          await callback.message.answer("❌ Отменено. Отправьте /post чтобы начать заново")

      async def send_preview(message: types.Message, state: FSMContext):
          data = await state.get_data()
          photos = data["photos"]

          # Ограничиваем до 10 фото
          if len(photos) > 10:
              photos = photos[:10]
              await state.update_data(photos=photos)

          name = data["name"]
          new_p = data["new_price"]
          old_p = data["old_price"]
          material = data["material"]
          sizes = data.get("sizes", "—")
          warehouse = data["warehouse"]
          category = data.get("category", "Только в основной канал")

          if warehouse == "Склад Москва":
              link_text = "Склад Москва"
              link_url = f"https://t.me/{MOSCOW_GROUP.lstrip('@')}"
              publish_main = False
              forward_to = MOSCOW_GROUP
          elif category == "Только в основной канал":
              link_text = "Все товары тут"
              link_url = f"https://t.me/{MAIN_CHANNEL.lstrip('@')}"
              publish_main = True
              forward_to = None
          else:
              link_text, forward_to = CATEGORY_MAP.get(category, ("Все товары", MAIN_CHANNEL))
              link_url = f"https://t.me/{forward_to.lstrip('@')}"
              publish_main = True

          post_text = (
              f"<b>{name}</b>\n\n"
              f"📏 <b>Размеры:</b> {sizes}\n\n"
              f"💸 <b>Цена:</b> {new_p}₽ <s>{old_p}₽</s>\n\n"
              f"🪴 <b>Материалы:</b> {material}\n\n"
              f"🚀 {warehouse}\n\n"
              f"🔗 <a href='{link_url}'>{link_text}</a>\n\n"
              f"🫶 Бесплатный обмен/возврат\n\n"
              f"💖 <a href='https://t.me/berishmotru'>Отзывы</a> | "
              f"<a href='https://telegra.ph/Pochemu-mozhno-doveryat-Berishmot-Store-04-20-2'>Гарантии</a>\n\n"
              f"Для заказа: @viktor_zorin"
          )

          await state.update_data(
              preview_text=post_text,
              preview_publish_main=publish_main,
              preview_forward_to=forward_to
          )

          # Формируем медиа-группу с ограничением до 10 фото
          media = [InputMediaPhoto(
              media=photos[0], 
              caption="🔍 <b>Превью поста</b>\n\n" + post_text, 
              parse_mode="HTML"
          )]

          # Добавляем только до 9 дополнительных фото (всего 10)
          for pid in photos[1:10]:
              media.append(InputMediaPhoto(media=pid))

          builder = InlineKeyboardBuilder()
          builder.button(text="✅ Опубликовать", callback_data="confirm_post")
          builder.button(text="❌ Отмена", callback_data="cancel")

          await bot.send_media_group(chat_id=message.chat.id, media=media)
          await message.answer("Подтверди публикацию:", reply_markup=builder.as_markup())

      @dp.callback_query(F.data == "confirm_post")
      async def confirm_publish(callback: types.CallbackQuery, state: FSMContext):
          data = await state.get_data()
          photos = data["photos"][:10]  # Гарантируем не более 10
          post_text = data["preview_text"]
          publish_main = data["preview_publish_main"]
          forward_to = data["preview_forward_to"]

          # Формируем финальную медиа-группу
          media = [InputMediaPhoto(media=photos[0], caption=post_text, parse_mode="HTML")]
          for pid in photos[1:10]:
              media.append(InputMediaPhoto(media=pid))

          try:
              if publish_main and MAIN_CHANNEL:
                  sent = await bot.send_media_group(chat_id=MAIN_CHANNEL, media=media)
                  await callback.message.answer(f"✅ Опубликовано в основной канал")

                  if forward_to:
                      await bot.send_media_group(chat_id=forward_to, media=media)
                      await callback.message.answer(f"✅ Продублировано в {forward_to}")
              elif forward_to:
                  await bot.send_media_group(chat_id=forward_to, media=media)
                  await callback.message.answer(f"✅ Опубликовано в {forward_to}")

              await state.clear()
          except Exception as e:
              await callback.message.answer(f"❌ Ошибка публикации: {e}")

      async def main():
          await dp.start_polling(bot)

      if __name__ == "__main__":
          asyncio.run(main())