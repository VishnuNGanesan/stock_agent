#!/bin/zsh
# Installs a macOS launchd job that wakes every hour and runs `main.py --scheduled`.
# The app itself decides whether a run is due (weekday, after RUN_AFTER_IST, not yet
# run today), so the schedule works in any timezone and catches up after your Mac sleeps.
#
# Extra flags are passed through to main.py, e.g.
#   ./scripts/install_schedule.sh --no-llm      # free daily screening, no Claude API calls
# Re-run the script without flags later to switch to the full AI run.
set -e
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.stockagent.daily"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

EXTRA_ARGS=""
for arg in "$@"; do
  EXTRA_ARGS+="
    <string>$arg</string>"
done

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PROJECT_DIR/.venv/bin/python</string>
    <string>$PROJECT_DIR/main.py</string>
    <string>--scheduled</string>$EXTRA_ARGS
  </array>
  <key>WorkingDirectory</key><string>$PROJECT_DIR</string>
  <key>StartInterval</key><integer>3600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$PROJECT_DIR/logs/launchd.log</string>
  <key>StandardErrorPath</key><string>$PROJECT_DIR/logs/launchd.log</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed $PLIST (flags: --scheduled $*)"
echo "Checks hourly; runs once per weekday after RUN_AFTER_IST (default 18:30 IST)."
echo "Logs: $PROJECT_DIR/logs/launchd.log and logs/run.log"
