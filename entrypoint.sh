#!/bin/sh
# entrypoint.sh — генерирует configs/ из переменных окружения перед запуском бота.
# Render/Fly/любой Docker хостинг: задайте секреты через env-переменные,
# этот скрипт запишет их в файлы конфигов.

set -e

mkdir -p /app/configs /app/logs /app/storage /app/storage/cache \
         /app/storage/plugins /app/storage/products /app/plugins

# ── _main.cfg ────────────────────────────────────────────────────────────────
cat > /app/configs/_main.cfg << EOF
[FunPay]
golden_key : ${FPC_GOLDEN_KEY}
user_agent : Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36
autoRaise : 1
autoResponse : 1
autoDelivery : 1
multiDelivery : 1
autoRestore : 0
autoDisable : 0
oldMsgGetMode : 0
locale : ru
keepSentMessagesUnread : 0

[Telegram]
enabled : 1
token : ${FPC_TG_TOKEN}
secretKeyHash : ${FPC_TG_SECRET_HASH}
blockLogin : 0
proxy :
owner_chat_id : ${FPC_TG_OWNER_CHAT_ID:-}

[BlockList]
blockDelivery : 0
blockResponse : 0
blockNewMessageNotification : 0
blockNewOrderNotification : 0
blockCommandNotification : 0

[NewMessageView]
includeMyMessages : 1
includeFPMessages : 1
includeBotMessages : 0
notifyOnlyMyMessages : 0
notifyOnlyFPMessages : 0
notifyOnlyBotMessages : 0
showImageName : 1

[SteamLibrary]
enabled : 0
steam_id : ${FPC_STEAM_ID:-}
manual_games_list : GTA V,RDR 2,Cyberpunk 2077,Elden Ring,BeamNG Drive
auto_create_lots : 1
lot_type : offline
rental_hours : 24
rental_price : 100
offline_price : 50
category_id :
mark_auto_delivery : 1
auto_raise_lots : 1
create_templates : 1

[Greetings]
ignoreSystemMessages : 0
onlyNewChats : 0
sendGreetings : 0
greetingsText : Привет, \$chat_name!
greetingsCooldown : 2

[OrderConfirm]
watermark : 1
sendReply : 1
replyText : \$username, спасибо за подтверждение заказа \$order_id! Если не сложно, оставь, пожалуйста, отзыв!

[ReviewReply]
star1Reply : 0
star2Reply : 0
star3Reply : 0
star4Reply : 0
star5Reply : 0
star1ReplyText :
star2ReplyText :
star3ReplyText :
star4ReplyText :
star5ReplyText : Спасибо что выбрали нас! Ждем вас снова ❤️ \$game

[Proxy]
enable : 0
proxy :
check : 0

[GmailGuard]
enabled : ${FPC_GMAIL_ENABLED:-0}
email : ${FPC_GMAIL_EMAIL:-}
appPassword : ${FPC_GMAIL_PASSWORD:-}

[Steam]
steam_id : ${FPC_STEAM_ID:-}
api_key : ${FPC_STEAM_API_KEY:-}

[Rental]
enabled : 1
default_rental_hours : 24
default_price : 100
check_interval : 60
auto_kick : 0
kick_method : notify

[SteamGuard]
profile_guard_enabled : 0
check_interval : 300

[Other]
watermark :
requestsDelay : 4
language : ru
EOF

# ── auto_response.cfg ─────────────────────────────────────────────────────────
if [ ! -f /app/configs/auto_response.cfg ]; then
  touch /app/configs/auto_response.cfg
fi

# ── auto_delivery.cfg ─────────────────────────────────────────────────────────
if [ ! -f /app/configs/auto_delivery.cfg ]; then
  touch /app/configs/auto_delivery.cfg
fi

# ── shared_secrets.cfg ───────────────────────────────────────────────────────
cat > /app/configs/shared_secrets.cfg << EOF
[meloed432]
username = meloed432
shared_secret = ${FPC_SS_MELOED432}
platform = steam
description = Основной аккаунт

[yura_yshakov]
username = yura_yshakov
shared_secret = ${FPC_SS_YURA}
identity_secret = ${FPC_IS_YURA:-}
steam_id = 76561198855363300
platform = steam
description = Импортировано из maFile
EOF

echo "✓ Конфиги сгенерированы. Запускаю бота..."
exec python main.py
