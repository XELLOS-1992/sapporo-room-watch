"""텔레그램 알림 발송.

토큰·chat_id 읽는 순서:
  1) 환경변수 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID  (GitHub Actions Secrets용)
  2) 같은 폴더의 telegram_config.json                (로컬 Mac용, gitignore됨)

사용: python3 notify.py "보낼 문구"
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG_PATH = Path(__file__).with_name("telegram_config.json")


def load_credentials():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat_id:
        return token, chat_id
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return cfg["bot_token"], str(cfg["chat_id"])
    raise RuntimeError("텔레그램 설정이 없습니다. setup_telegram.py를 먼저 실행하세요.")


def api(token, method, params=None, timeout=30):
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params or {}).encode()
    with urllib.request.urlopen(url, data=data, timeout=timeout) as resp:
        body = json.loads(resp.read().decode())
    if not body.get("ok"):
        raise RuntimeError(f"텔레그램 API 오류: {body}")
    return body["result"]


def send(text, disable_preview=False):
    token, chat_id = load_credentials()
    return api(token, "sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true" if disable_preview else "false",
    })


if __name__ == "__main__":
    msg = " ".join(sys.argv[1:]) or "숙소 빈방 알림 테스트입니다."
    send(msg)
    print("발송 완료")
