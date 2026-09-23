import asyncio
from io import BytesIO
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import main


class FakeState:
    def __init__(self):
        self.data = {}
        self.current = None

    async def clear(self):
        self.data = {}
        self.current = None

    async def set_state(self, state):
        self.current = state.state

    async def get_state(self):
        return self.current

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kwargs):
        self.data.update(kwargs)


class FakeMessage:
    def __init__(self, message_id, caption=None, album="album"):
        self.message_id = message_id
        self.caption = caption
        self.media_group_id = album
        self.chat = SimpleNamespace(id=123)
        self.from_user = SimpleNamespace(id=456)
        self.photo = [SimpleNamespace(file_id=f"photo-{message_id}")]
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append(text)


class PhotoAlbumTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.state = FakeState()
        self.delay = patch.object(main, "ALBUM_SETTLE_SECONDS", 0.03)
        self.delay.start()
        self.addAsyncCleanup(self._cleanup)
        await main.cmd_post(FakeMessage(1, album=None), self.state)

    async def _cleanup(self):
        self.delay.stop()
        await asyncio.sleep(0.04)
        main._pending_albums.clear()

    async def test_shuffled_album_preserves_first_five_for_vk(self):
        photos = [
            FakeMessage(100 + i, "Пуховик\n18990\nМатериал" if i == 0 else None)
            for i in range(9)
        ]
        with patch.object(main, "show_category_inline", new_callable=AsyncMock):
            for index in [4, 2, 8, 1, 6, 3, 7, 5, 0]:
                await main.process_photos_with_caption(photos[index], self.state)
            self.assertEqual(self.state.current, main.PostForm.waiting_for_photos_and_text.state)
            await asyncio.sleep(0.06)

        expected = [f"photo-{100 + i}" for i in range(9)]
        self.assertEqual(self.state.data["photos"], expected)
        self.assertEqual(self.state.current, main.PostForm.waiting_for_category.state)

        with patch.object(main.bot, "send_media_group", new_callable=AsyncMock) as send_album:
            await main._do_publish(
                expected, "Пуховик", 18990, 22000, "Материал", "M", "Зима", "Зима",
                None, {"main": True, "sub": False, "moscow": False},
            )
            sent = send_album.call_args.args[1]
            self.assertEqual([item.media for item in sent], expected)

        async def download(file_path):
            return BytesIO(file_path.encode())

        with (
            patch.object(main.bot, "get_file", new_callable=AsyncMock) as get_file,
            patch.object(main.bot, "download_file", side_effect=download),
            patch.object(main.pe, "add_product", new_callable=AsyncMock) as pinterest,
            patch.object(main.vke, "add_offer", new_callable=AsyncMock) as vk,
        ):
            get_file.side_effect = lambda file_id: SimpleNamespace(file_path=file_id)
            pinterest.return_value = (1, 1)
            vk.return_value = (1, 1)
            await main._add_to_catalogs(
                expected, "Пуховик", "Зима", material="Полиэстер",
                to_pinterest=True, to_vk=True,
            )
            self.assertEqual(pinterest.call_args.args[0], [b"photo-100"])
            self.assertEqual(vk.call_args.args[0], [f"photo-{100 + i}".encode() for i in range(5)])
            self.assertEqual(vk.call_args.args[1], main.visual_name("Пуховик"))
            self.assertEqual(vk.call_args.args[3], "Зима")
            self.assertEqual(vk.call_args.args[5], "Полиэстер")

    async def test_old_album_cannot_modify_new_post(self):
        with patch.object(main, "show_category_inline", new_callable=AsyncMock):
            await main.process_photos_with_caption(
                FakeMessage(100, "Пуховик\n18990", album="old"), self.state
            )
            await main.cmd_post(FakeMessage(200, album=None), self.state)
            await asyncio.sleep(0.06)
        self.assertEqual(self.state.current, main.PostForm.waiting_for_photos_and_text.state)
        self.assertNotIn("photos", self.state.data)

    async def test_polling_retries_after_network_timeout(self):
        with (
            patch.object(
                main.dp,
                "start_polling",
                new_callable=AsyncMock,
                side_effect=[asyncio.TimeoutError(), None],
            ) as polling,
            patch.object(main.asyncio, "sleep", new_callable=AsyncMock) as sleep,
        ):
            await main._poll_telegram_forever()

        self.assertEqual(polling.await_count, 2)
        sleep.assert_awaited_once_with(5)