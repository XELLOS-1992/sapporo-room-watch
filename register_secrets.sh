#!/bin/zsh
# 로컬 telegram_config.json의 봇 토큰과 chat_id를 GitHub Actions Secrets에 등록한다.
# 값은 화면에 출력하지 않는다.
set -e
cd "${0:A:h}"
python3 -c "import json;print(json.load(open('telegram_config.json'))['bot_token'])" | gh secret set TELEGRAM_BOT_TOKEN
python3 -c "import json;print(json.load(open('telegram_config.json'))['chat_id'])" | gh secret set TELEGRAM_CHAT_ID
echo
gh secret list
echo "완료: 위 목록에 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID가 보이면 성공입니다."
