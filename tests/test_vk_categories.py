from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

import vk_export as vk


class VkCategoryTests(TestCase):
    def test_winter_puffer_takes_priority_over_leather_trim(self):
        self.assertIn((50082, "Пуховики и зимние куртки"), vk.VK_CATEGORIES)
        self.assertEqual(vk._category_id("Зима", "Пуховик Moncler", ""), 50082)
        self.assertEqual(
            vk._category_id("Зима", "Пуховик Moncler", "Замша и кожа на отделке"),
            50082,
        )
        self.assertEqual(vk._category_id("Куртки и Ветровки", "Пуховик Moncler", ""), 50082)

    def test_leather_and_bot_category_fallback(self):
        self.assertEqual(vk._category_id("Зима", "Дубленка", "Натуральная кожа"), 50076)
        self.assertEqual(vk._category_id("Куртки и Ветровки", "Куртка", "Замша"), 50076)
        self.assertEqual(vk._category_id("Зима", "Зимняя куртка", "Полиэстер"), 50082)
        self.assertEqual(vk._category_id("Футболки и Рубашки", "Футболка", ""), 40087)

    def test_manager_link_is_between_material_and_reviews(self):
        description = vk._build_description("M–XL", "Полиэстер")
        manager_line = f"Для оформления заказа пишите: {vk.MANAGER_URL}"
        self.assertEqual(description.count(manager_line), 1)
        self.assertLess(description.index("Размеры: M–XL"), description.index("Полиэстер"))
        self.assertLess(description.index(vk.MATERIALS_NOTE), description.index(manager_line))
        self.assertLess(description.index(manager_line), description.index("Отзывы:"))
        self.assertLess(description.index("Отзывы:"), description.index("Гарантии:"))
        self.assertEqual(vk.SHOP_URL, vk.MANAGER_URL)
        self.assertEqual(vk.PRODUCT_URL, vk.MANAGER_URL)


class VkOfferTests(IsolatedAsyncioTestCase):
    async def test_add_offer_passes_name_and_material_to_category_detection(self):
        with (
            patch.object(vk, "_storage"),
            patch.object(vk, "_read_offers", return_value=[]),
            patch.object(vk, "_append_offer") as append_offer,
        ):
            added, _ = await vk.add_offer(
                [b"photo"], "Пуховик Moncler", 18990, "Зима", "M–XL",
                "Кожа на отделке",
            )
        offer = append_offer.call_args.args[0]
        self.assertEqual(added, 1)
        self.assertEqual(offer["category_id"], 50082)
        self.assertEqual(offer["name"], "Пуховик Moncler")
        self.assertIn("Кожа на отделке", offer["description"])