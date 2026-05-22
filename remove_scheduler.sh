#!/bin/bash

AGENTS_DIR="$HOME/Library/LaunchAgents"
UPLOADER_LABEL="com.harishankargiri.naukri-uploader"
APPLIER_LABEL="com.harishankargiri.naukri-applier"

echo ""
echo "  Removing Naukri schedulers..."

for LABEL in "$UPLOADER_LABEL" "$APPLIER_LABEL"; do
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
