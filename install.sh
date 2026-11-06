#!/bin/sh
# Установка/обновление моста Yeelight Pro <-> Loxone на Wiren Board.
# Запуск на контроллере:  sh install.sh
# Рядом с этим скриптом должен лежать yeelight_bridge.py (остальное создастся само).
set -e

DEST=/mnt/data/yeelight-bridge
UNIT=/etc/systemd/system/yeelight-bridge.service

if [ ! -f yeelight_bridge.py ]; then
    echo "Ошибка: рядом нет yeelight_bridge.py" >&2
    exit 1
fi

mkdir -p "$DEST"
cp yeelight_bridge.py "$DEST/yeelight_bridge.py"

if [ ! -f "$DEST/config.json" ]; then
    cat > "$DEST/config.json" <<'CFG'
{
  "gateway_ip": "192.168.1.53",
  "gateway_port": 65443,
  "loxone_ip": "192.168.1.91",
  "loxone_port": 7002,
  "command_port": 7002,
  "poll_interval": 30,
  "transition_ms": 500,
  "aliases": {}
}
CFG
    echo "Создан конфиг по умолчанию: $DEST/config.json"
fi

cat > "$UNIT" <<'UNIT'
[Unit]
Description=Yeelight Pro <-> Loxone UDP bridge
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/python3 /mnt/data/yeelight-bridge/yeelight_bridge.py --config /mnt/data/yeelight-bridge/config.json
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now yeelight-bridge

sleep 3
if systemctl is-active --quiet yeelight-bridge; then
    echo "OK: мост запущен."
else
    echo "Сервис не поднялся, последние строки журнала:" >&2
    journalctl -u yeelight-bridge -n 20 --no-pager >&2 || true
    exit 1
fi

IP=$(ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1 | head -1)
echo
echo "Веб-интерфейс моста:  http://${IP}:8082/"
echo "Журнал:               journalctl -u yeelight-bridge -f"
echo
echo "Дальше: откройте веб-интерфейс и укажите IP шлюза Yeelight Pro,"
echo "IP Miniserver и при необходимости алиасы узлов, затем нажмите"
echo "'Сохранить и перезапустить'."
