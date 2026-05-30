#!/bin/bash
BOT_TOKEN="8968930119:AAE5p8Egja2ZMGo59QA6-Wywkeywnjstacc"
PORT=8899
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Запускаю сервер..."
python3 "$DIR/server.py" &
SERVER_PID=$!
sleep 2

echo "Запускаю туннель Cloudflare..."
cloudflared tunnel --url "http://localhost:$PORT" --no-autoupdate 2>&1 | tee /tmp/cf_tunnel.log &
CF_PID=$!

sleep 5
URL=$(grep -oP 'https://[a-z0-9\-]+\.trycloudflare\.com' /tmp/cf_tunnel.log | head -1)

if [ -z "$URL" ]; then
  echo "Жду URL туннеля..."
  sleep 8
  URL=$(grep -oP 'https://[a-z0-9\-]+\.trycloudflare\.com' /tmp/cf_tunnel.log | head -1)
fi

if [ -z "$URL" ]; then
  echo "❌ Не удалось получить URL туннеля"
  kill $SERVER_PID $CF_PID 2>/dev/null
  exit 1
fi

echo "✅ Мини-приложение доступно: $URL"

# Обновляем кнопку /app в боте
curl -s -X POST "https://api.telegram.org/bot$BOT_TOKEN/setMyCommands" \
  -H "Content-Type: application/json" \
  -d "{\"commands\":[
    {\"command\":\"start\",\"description\":\"🚀 Запустить бота\"},
    {\"command\":\"app\",\"description\":\"🎮 Открыть мини-приложение\"},
    {\"command\":\"deals\",\"description\":\"🔥 Все скидки Steam\"},
    {\"command\":\"setsteam\",\"description\":\"✅ Steam привязан\"},
    {\"command\":\"stop\",\"description\":\"❌ Отписаться\"}
  ]}" > /dev/null

# Сохраняем URL для бота
echo "$URL" > /tmp/webapp_url.txt

echo "Нажми Ctrl+C чтобы остановить"
wait $CF_PID
kill $SERVER_PID 2>/dev/null
