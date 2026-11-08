#!/bin/zsh
# Mac launchd용: 최신 코드를 받고 네이버 감시를 1회 실행한다.
cd "${0:A:h}"
git pull -q --ff-only 2>/dev/null || true
STATE_FILE=state_mac.json RUNNER_LABEL="Mac" /usr/bin/python3 watch.py
