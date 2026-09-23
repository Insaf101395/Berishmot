import re
from datetime import datetime
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import pinterest_export as pe


class PinterestLinkTests(IsolatedAsyncioTestCase):
    async def test_two_products_have_distinct_links_even_after_clear(self):
        rows = []

        def archive_and_clear(kind, archived_name, records):
            self.assertEqual(kind, "pinterest")
            self.assertEqual(len(records), 2)
            rows.clear()

        with (
            patch.object(pe, "_generated_pin_links", set()),
            patch.object(pe, "datetime") as clock,
            patch.object(pe.secrets, "randbelow", side_effect=[7, 7, 8, 7, 9]),
            patch.object(pe, "_rows", side_effect=lambda: list(rows)),
            patch.object(pe, "_append_rows", side_effect=rows.extend),
            patch.object(pe, "_write_csv"),
            patch.object(pe.batch_storage, "archive_and_clear", side_effect=archive_and_clear),
            patch.object(pe, "upload_to_imgbb", new_callable=AsyncMock) as upload,
            patch.object(pe, "generate_pin_copy", new_callable=AsyncMock) as copy,
        ):
            clock.now.return_value = datetime(2026, 9, 23, 15, 30, 10)
            upload.return_value = "https://i.example/photo.jpg"
            copy.return_value = {
                "title": "Зимняя куртка",
                "description": "Описание",
                "keywords": "зима",
            }

            first_count, _ = await pe.add_product([b"photo1"], "Зимняя куртка", "Зима")
            first_row = dict(rows[0])
            second_count, _ = await pe.add_product([b"photo2"], "Зимняя куртка", "Зима")
            second_row = dict(rows[1])
            pe.clear_batch()
            third_count, _ = await pe.add_product([b"photo3"], "Зимняя куртка", "Зима")
            third_row = dict(rows[0])

        self.assertEqual((first_count, second_count, third_count), (1, 1, 1))
        self.assertEqual(first_row["Link"], "https://berishmot.store/?pin=20260923153010-007")
        self.assertEqual(second_row["Link"], "https://berishmot.store/?pin=20260923153010-008")
        self.assertEqual(third_row["Link"], "https://berishmot.store/?pin=20260923153010-009")
        self.assertNotEqual(first_row["Link"], second_row["Link"])
        for row in (first_row, second_row, third_row):
            self.assertRegex(row["Link"], re.compile(r"\?pin=\d{14}-\d{3}$"))
            self.assertEqual(row["Title"], "Зимняя куртка")
            self.assertEqual(row["Media URL"], "https://i.example/photo.jpeg")
            self.assertEqual(row["Pinterest board"], pe.BOARD_MAP["Зима"])
            self.assertEqual(row["Description"], "Описание")
            self.assertEqual(row["Keywords"], "зима")
            self.assertEqual(row["Publish date"], "")