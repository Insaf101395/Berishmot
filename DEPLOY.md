# Перенос бота Berishmot на свой сервер (без Replit)

Бот больше не зависит от Replit: хранилище (`replit.object_storage`) заменено
на локальные файлы (`local_object_storage.py`, папка `storage_data/`).
Один процесс (`app.py` → `main.py`) поднимает и Telegram-бота, и веб-сервер,
который отдаёт `/vk_batch.xml` (фид для VK) и `/img/<hash>.jpeg` (фото).

## Что нужно
- VPS с Ubuntu 22.04+ (Timeweb / Aeza / Selectel / Hetzner и т.п.), ~300–400 ₽/мес.
- Домен для фото/фида, напр. `img.berishmot.store` (поддомен вашего `berishmot.store`).
- Значения из Replit Secrets: BOT_TOKEN, MAIN_CHANNEL, ANTHROPIC_API_KEY,
  IMGBB_KEY, VK_GROUP_ID, SESSION_SECRET.

## Шаги (копируйте по одной команде)

### 1. Поставить Python и nginx
```bash
sudo apt update && sudo apt install -y python3-venv python3-pip nginx git certbot python3-certbot-nginx
```

### 2. Забрать код
```bash
sudo mkdir -p /opt/berishmot && sudo chown $USER /opt/berishmot
git clone https://github.com/Insaf101395/berishmot /opt/berishmot
cd /opt/berishmot
```

### 3. Виртуальное окружение и зависимости
```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

### 4. Настроить переменные окружения
```bash
cp .env.example .env
nano .env   # вписать BOT_TOKEN, MAIN_CHANNEL, ANTHROPIC_API_KEY, IMGBB_KEY, VK_GROUP_ID, SESSION_SECRET, PUBLIC_URL
```
`PUBLIC_URL=https://img.berishmot.store` — по этому адресу VK будет брать фото и фид.
`DISABLE_BOT_POLLING` НЕ добавляйте — иначе бот не отвечает.

### 5. Служба systemd (бот сам стартует и перезапускается)
```bash
sudo cp deploy/berishmot-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now berishmot-bot
sudo systemctl status berishmot-bot     # должно быть active (running)
journalctl -u berishmot-bot -f          # логи, для проверки
```

### 6. Домен + nginx + HTTPS
- В DNS вашего домена добавьте A-запись `img` → IP сервера.
```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/berishmot
sudo ln -s /etc/nginx/sites-available/berishmot /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d img.berishmot.store   # бесплатный HTTPS
```

### 7. Проверка
- Откройте `https://img.berishmot.store/vk_batch.xml` — должен отдаться XML-фид.
- Напишите боту в Telegram — должен отвечать.
- В VK (Товары → Добавить → ссылка на файл) укажите
  `https://img.berishmot.store/vk_batch.xml`.

## Перенос старых фото и партий (по желанию)
Старые VK-фото и партии лежат в Replit App Storage (папки `vk_images/`,
`export_batches/`). Чтобы не терять текущую витрину VK:
1. Пока Replit оплачен — скачайте содержимое App Storage.
2. Разложите в `storage_data/` на сервере с теми же путями
   (`storage_data/vk_images/...`, `storage_data/export_batches/...`).
3. Перегенерируйте фид (`/vk_batch.xml`) и переимпортируйте в VK — теперь ссылки
   будут на `img.berishmot.store`, независимо от Replit.

Если старые фото не переносить — новые товары всё равно пойдут корректно,
только уже с вашего домена.

## Заметки
- Pinterest-фото по-прежнему уходят на ImgBB (`i.ibb.co`) — это не зависит от Replit.
- Файлы `.replit`, `run.replit`, `client/`, `server/`, vite/drizzle — от Replit-обвязки,
  для работы бота на сервере не нужны (можно не трогать).
