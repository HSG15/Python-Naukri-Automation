#!/bin/bash

AGENTS_DIR="$HOME/Library/LaunchAgents"
UPLOADER_LABEL="com.harishankargiri.naukri-uploader"
APPLIER_LABEL="com.harishankargiri.naukri-applier"
WEEKLY_LABEL="com.harishankargiri.naukri-weekly-summary"
RECOMMENDED_LABEL="com.harishankargiri.naukri-recommended"
WATCHER_LABEL="com.harishankargiri.naukri-watcher"

echo ""
echo "  Removing Naukri schedulers..."

for LABEL in "$UPLOADER_LABEL" "$APPLIER_LABEL" "$WEEKLY_LABEL" "$RECOMMENDED_LABEL" "$WATCHER_LABEL"; do
    PLIST="$AGENTS_DIR/$LABEL.plist"
    if launchctl list | grep -q "$LABEL"; then
        launchctl unload "$PLIST" 2>/dev/null
        echo "  ✅ Unloaded: $LABEL"
    fi
    if [ -f "$PLIST" ]; then
        rm "$PLIST"
        echo "  🗑  Removed: $PLIST"
    fi
done

echo ""
echo "  ✅ All Naukri schedulers removed."
