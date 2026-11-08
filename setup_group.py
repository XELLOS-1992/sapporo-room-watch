"""알림 대상을 텔레그램 그룹방으로 변경 (1회 실행).

봇이 그룹에 초대되었거나 그룹에서 메시지가 오면 그 그룹의 chat_id를 감지해
telegram_config.json의 chat_id를 교체하고 그룹에 시험 메시지를 보낸다.
토큰은 이미 저장된 설정 파일에서 읽는다.
"""
import json
import os
import time

from notify import CONFIG_PATH, api, load_credentials

WAIT_SECONDS = 600
GROUP_TYPES = {"group", "supergroup"}


def find_group(update):
    for key in ("my_chat_member", "message", "edited_message"):
        obj = update.get(key)
        if not obj:
            continue
        chat = obj.get("chat", {})
        if chat.get("type") not in GROUP_TYPES:
            continue
        # 일반 그룹 → 슈퍼그룹 전환 시 새 id를 따른다
        if obj.get("migrate_to_chat_id"):
            return {"id": obj["migrate_to_chat_id"], "title": chat.get("title", "")}
        if key == "my_chat_member" and obj["new_chat_member"]["status"] in ("left", "kicked"):
            continue
        return chat
    return None


def main():
    token, old_chat_id = load_credentials()
    print(f"그룹 감지 대기 중 (최대 {WAIT_SECONDS // 60}분). 봇을 그룹에 추가하고 그룹에 아무 메시지나 보내주세요.")
    offset = None
    group = None
    deadline = time.time() + WAIT_SECONDS
    while time.time() < deadline and group is None:
        params = {"timeout": 25, "allowed_updates": json.dumps(["message", "my_chat_member"])}
        if offset is not None:
            params["offset"] = offset
        try:
            updates = api(token, "getUpdates", params, timeout=35)
        except Exception as e:
            print(f"  재시도 중... ({e})")
            time.sleep(3)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            group = find_group(u) or group
    if group is None:
        print("시간 안에 그룹을 찾지 못했습니다.")
        return 1
    api(token, "getUpdates", {"offset": offset, "timeout": 0})

    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    cfg["private_chat_id"] = cfg.get("private_chat_id", old_chat_id)
    cfg["chat_id"] = group["id"]
    CONFIG_PATH.write_text(json.dumps(cfg), encoding="utf-8")
    os.chmod(CONFIG_PATH, 0o600)
    print(f"그룹 연결됨: {group.get('title', '')} (chat_id {group['id']})")

    api(token, "sendMessage", {
        "chat_id": group["id"],
        "text": "✅ 이 그룹방이 삿포로 숙소 빈방 알림 수신방으로 설정되었어요.\n대상 숙소에 빈방이 생기면 여기로 알려드립니다.",
    })
    print("그룹 시험 메시지 발송 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
