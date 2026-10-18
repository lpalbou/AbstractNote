#!/bin/zsh
# deploy-watch.sh --gateway URL --token TOKEN [--user ID] [--screen NAME] [--no-build] [--forget]
# Signed Debug build for the paired physical watch, install over CoreDevice, launch signed in: the
# gateway and token go in as launch arguments and the app keeps them in its sealed sign-in file
# (ADR 0006), so nothing is typed on the wrist. Without --gateway/--token the sign-in already on the
# watch stays; --forget removes it (the sign-in screen shows).
# Needs: the watch paired for development (docs/pairing.md) and its CoreDevice id in WATCH_DEVICE or
# ../../untracked/watch-device-id.txt (xcrun devicectl list devices). Use a user account, never an admin token.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEVICE="${WATCH_DEVICE:-$(head -1 "$ROOT/../../untracked/watch-device-id.txt" 2>/dev/null)}"
[ -n "$DEVICE" ] || { echo "no watch: set WATCH_DEVICE=<CoreDevice id> (xcrun devicectl list devices)"; exit 1; }
DERIVED_DATA="${DERIVED_DATA:-$ROOT/build/DerivedData}"
BUILD=1; ARGS=(); GW=""; TOKEN=""
while [ $# -gt 0 ]; do case "$1" in
  --gateway) GW="$2"; shift 2;;  --token) TOKEN="$2"; shift 2;;  --user) ARGS+=(--user "$2"); shift 2;;
  --screen) ARGS+=(--screen "$2"); shift 2;;  --forget) ARGS+=(--forget-gateway); shift;;  --no-build) BUILD=0; shift;;
  *) echo "unknown argument $1"; exit 2;; esac; done
if [ -n "$GW" ] || [ -n "$TOKEN" ]; then
  [ -n "$GW" ] && [ -n "$TOKEN" ] || { echo "--gateway and --token go together"; exit 2; }
  ARGS+=(--gateway "$GW" --token "$TOKEN")
fi
cd "$ROOT"
if [ $BUILD = 1 ]; then
  [ project.yml -nt AbstractNoteWatch.xcodeproj/project.pbxproj ] && xcodegen generate --spec project.yml
  xcodebuild -project AbstractNoteWatch.xcodeproj -scheme AbstractNoteWatch -destination 'generic/platform=watchOS' \
    -configuration Debug -derivedDataPath "$DERIVED_DATA" -allowProvisioningUpdates -quiet build
  xcrun devicectl device install app --device "$DEVICE" "$DERIVED_DATA/Build/Products/Debug-watchos/AbstractNoteWatch.app" --timeout 180 >/dev/null
fi
xcrun devicectl device process launch --device "$DEVICE" --terminate-existing --timeout 60 ai.abstractframework.note.watch "${ARGS[@]}" >/dev/null
echo "launched${GW:+ against $GW}"
