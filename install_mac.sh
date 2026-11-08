#!/bin/zsh
# Mac 자동 실행(launchd) 등록: 20분마다 run_mac.sh 실행. 해제는 uninstall 인자로.
set -e
R="${0:A:h}"
L="com.user.sapporo-room-watch"
P="$HOME/Library/LaunchAgents/$L.plist"
if [[ "$1" == "uninstall" ]]; then
  launchctl bootout gui/$(id -u)/$L 2>/dev/null || true
  rm -f "$P"; echo "해제 완료"; exit 0
fi
mkdir -p "$R/logs"
cat > "$P" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$L</string>
  <key>ProgramArguments</key>
  <array><string>/bin/zsh</string><string>$R/run_mac.sh</string></array>
  <key>WorkingDirectory</key><string>$R</string>
  <key>StartInterval</key><integer>1200</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$R/logs/watch.out.log</string>
  <key>StandardErrorPath</key><string>$R/logs/watch.err.log</string>
</dict>
</plist>
PL
plutil -lint "$P" >/dev/null
launchctl bootout gui/$(id -u)/$L 2>/dev/null || true
launchctl bootstrap gui/$(id -u) "$P"
echo "등록 완료: 20분마다 실행 (로그: $R/logs/watch.out.log)"
