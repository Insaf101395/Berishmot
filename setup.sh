#!/usr/bin/env bash
# Berishmot — установка бота одной командой на чистый Ubuntu 22.04 (root).
# Также открывает SSH на порту 2222 (обход блокировки порта 22 у провайдера).
set -e

echo "==> [1/6] SSH на порт 2222 (в дополнение к 22)"
if ! grep -q "^Port 2222" /etc/ssh/sshd_config; then
  printf '\nPort 22\nPort 2222\n' >> /etc/ssh/sshd_config
fi
systemctl restart ssh 2>/dev/null || systemctl restart sshd 2>/dev/null || true

echo "==> [2/6] Пакеты (python, nginx, git)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3-venv python3-pip nginx git

echo "==> [3/6] Код бота"
if [ ! -d /opt/berishmot/.git ]; then
  git clone -b claude/migrate-off-replit https://github.com/Insaf101395/berishmot /opt/berishmot
else
  cd /opt/berishmot && git fetch origin && git checkout claude/migrate-off-replit && git pull
fi
cd /opt/berishmot

echo "==> [4/6] Виртуальное окружение и зависимости"
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

echo "==> [5/6] Файл .env (шаблон)"
[ -f .env ] || cp .env.example .env

echo "==> [6/6] Служба systemd"
cp deploy/berishmot-bot.service /etc/systemd/system/ 2>/dev/null || true
systemctl daemon-reload

echo ""
echo "======================================================"
echo " ГОТОВО. Осталось заполнить секреты и запустить бота."
echo " 1) Зайдите по SSH на порт 2222 (без VPN):"
echo "    ssh -p 2222 root@$(hostname -I | awk '{print $1}')"
echo " 2) Впишите секреты:  nano /opt/berishmot/.env"
echo " 3) Запустите бота:   systemctl enable --now berishmot-bot"
echo "======================================================"
