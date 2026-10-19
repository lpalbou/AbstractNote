#!/usr/bin/env bash
# Generate, test, build for the watch simulator, install, and screenshot every screen (sample notebook).
# Usage: scripts/build.sh [--no-screens]
# Environment:
#   SIM_ID          simulator id (default: the first available "Apple Watch Series 11 (46mm)", booted first)
#   DERIVED_DATA    Xcode DerivedData (default: build/DerivedData, ignored by git)
#   SPM_SCRATCH     `swift test` build folder (default: the package's .build)
#   SCREENS_DIR     where screenshots go (default: ../../untracked/screens/watch, outside git)
#   KEEP_APP=1      leave the app installed on the simulator afterwards
# The simulator may be shared: the script waits while any other xcodebuild runs (max 30 min), never
# shuts it down, never erases it, and uninstalls only its own app.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SIM="${SIM:-Apple Watch Series 11 (46mm)}"
BUNDLE=ai.abstractframework.note.watch
DERIVED_DATA="${DERIVED_DATA:-$ROOT/build/DerivedData}"
SPM_SCRATCH="${SPM_SCRATCH:-}"
SCREENS_DIR="${SCREENS_DIR:-$ROOT/../../untracked/screens/watch}"
SCREENS=(signin note note-sending note-saved note-error ask ask-thinking ask-answer ask-deeper today today-quiet insight loop cue cue-why note-detail settings about)
t() { date +%s; }

# Another xcodebuild (another session's UI tests on the same simulator): wait, bounded.
wait_for_xcodebuild() {
  local waited=0
  while pgrep -x xcodebuild >/dev/null; do
    [ $waited -ge 1800 ] && { echo "another xcodebuild has run for 30 min; giving up"; exit 1; }
    [ $waited -eq 0 ] && echo "waiting for another xcodebuild to finish ($(pgrep -lx xcodebuild | tr '\n' ' '))"
    sleep 60; waited=$((waited + 60))
  done
}

if [ -z "${SIM_ID:-}" ]; then
  # By id: Xcode 27's name lookup fails when several runtimes are installed. A booted one first.
  SIM_ID="$(xcrun simctl list devices available | grep -F "$SIM (" | grep -F "(Booted)" | head -1 | grep -oE '[0-9A-F]{8}-[0-9A-F-]{27}' || true)"
  [ -n "$SIM_ID" ] || SIM_ID="$(xcrun simctl list devices available | grep -F "$SIM (" | head -1 | grep -oE '[0-9A-F]{8}-[0-9A-F-]{27}' || true)"
fi
[ -n "$SIM_ID" ] || { echo "no simulator named $SIM (xcrun simctl list devices)"; exit 1; }

T0=$(t)
cd "$ROOT"
xcodegen generate --spec project.yml
(cd Packages/AbstractGatewayClient && swift test ${SPM_SCRATCH:+--scratch-path "$SPM_SCRATCH"})
T1=$(t)
wait_for_xcodebuild
xcodebuild -project AbstractNoteWatch.xcodeproj -scheme AbstractNoteWatch \
  -destination "platform=watchOS Simulator,id=$SIM_ID" \
  -configuration Debug -derivedDataPath "$DERIVED_DATA" build CODE_SIGNING_ALLOWED=NO -quiet
T2=$(t)
echo "swift test: $((T1-T0)) s (incl. generate) · xcodebuild: $((T2-T1)) s"
APP="$DERIVED_DATA/Build/Products/Debug-watchsimulator/AbstractNoteWatch.app"
test "$(plutil -extract WKWatchOnly raw "$APP/Info.plist")" = "true"
[[ "${1:-}" == "--no-screens" ]] && exit 0

xcrun simctl list devices | grep -F "$SIM_ID" | grep -qF "(Booted)" || xcrun simctl boot "$SIM_ID"
xcrun simctl bootstatus "$SIM_ID" >/dev/null
wait_for_xcodebuild
xcrun simctl install "$SIM_ID" "$APP"
mkdir -p "$SCREENS_DIR"
for s in "${SCREENS[@]}"; do
  wait_for_xcodebuild
  xcrun simctl terminate "$SIM_ID" "$BUNDLE" 2>/dev/null || true
  # The exit code matters: a failed launch still yields a (watch-face) screenshot.
  if ! xcrun simctl launch "$SIM_ID" "$BUNDLE" --mock --screen "$s" >/dev/null; then
    echo "launch failed for screen $s"; exit 1
  fi
  sleep 5
  xcrun simctl io "$SIM_ID" screenshot "$SCREENS_DIR/$s.png" >/dev/null 2>&1
  echo "screenshot: $SCREENS_DIR/$s.png"
done
xcrun simctl terminate "$SIM_ID" "$BUNDLE" 2>/dev/null || true
[ "${KEEP_APP:-0}" = 1 ] || xcrun simctl uninstall "$SIM_ID" "$BUNDLE"
echo "total: $(( $(t) - T0 )) s"
