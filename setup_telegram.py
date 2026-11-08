"""새 텔레그램 봇 연결 (1회 실행).

1) 봇 토큰을 화면에 보이지 않게 입력받아 getMe로 확인
2) 사용자가 텔레그램에서 봇에게 /start 를 보내면 chat_id 감지
3) telegram_config.json(권한 600)에 저장하고 시험 메시지 발송
"""
import getpass
import json
import os
import re
import time
import urllib.error

from notify import CONFIG_PATH, api

WAIT_SECONDS = 300


def main():
    raw = getpass.getpass("BotFather가 준 봇 토큰을 붙여넣고 Enter (화면에 안 보임): ")
    # 붙여넣을 때 섞여 들어온 한글·공백·보이지 않는 문자를 버리고 토큰 형태만 추출
    m = re.search(r"\d{6,}:[A-Za-z0-9_-]{30,}", raw)
    if not m:
        print(f"토큰 형태(숫자:영문)를 찾지 못했습니다. 입력 길이 {len(raw)}자. 다시 실행해 주세요.")
        return 1
    token = m.group(0)
    try:
        me = api(token, "getMe")
    except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError) as e:
        print(f"토큰 확인 실패: {e}")
        return 1
    username = me["username"]
    print(f"\n봇 확인됨: @{username}")
    print(f"텔레그램에서 https://t.me/{username} 을 열고 [시작] 또는 /start 를 보내주세요.")
    print(f"(최대 {WAIT_SECONDS // 60}분 대기)")

    offset = None
    deadline = time.time() + WAIT_SECONDS
    chat = None
    while time.time() < deadline and chat is None:
        params = {"timeout": 25}
        if offset is not None:
            params["offset"] = offset
        try:
            updates = api(token, "getUpdates", params, timeout=35)
        except Exception as e:  # 네트워크 일시 오류는 재시도
            print(f"  재시도 중... ({e})")
            time.sleep(3)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            msg = u.get("message") or {}
            if msg.get("chat", {}).get("type") == "private":
                chat = msg["chat"]
                break

    if chat is None:
        print("시간 안에 /start 메시지를 받지 못했습니다. 다시 실행해 주세요.")
        return 1

    # 받은 업데이트를 확인 처리
    api(token, "getUpdates", {"offset": offset, "timeout": 0})

    CONFIG_PATH.write_text(json.dumps({"bot_token": token, "chat_id": chat["id"]}), encoding="utf-8")
    os.chmod(CONFIG_PATH, 0o600)
    name = chat.get("first_name", "")
    print(f"\n대화방 연결됨: {name} (chat_id {chat['id']}) → {CONFIG_PATH.name} 저장")

    api(token, "sendMessage", {
        "chat_id": chat["id"],
        "text": "✅ 삿포로 숙소 빈방 알림 봇 연결 완료\n앞으로 감시 대상 숙소에 빈방이 생기면 여기로 알려드릴게요.",
    })
    print("시험 메시지 발송 완료. 텔레그램을 확인하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
