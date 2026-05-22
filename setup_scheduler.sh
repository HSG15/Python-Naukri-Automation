#!/bin/bash

# =============================================================================
# setup_scheduler.sh — Naukri Smart Job Applier + Resume Uploader
#
# Creates TWO separate launchd agents:
#
#   1. com.harishankargiri.naukri-uploader
#      Runs FULL cycle (resume upload + headline + apply) at:
#        → 09:15 AM  and  02:00 PM
#
#   2. com.harishankargiri.naukri-applier
#      Runs APPLY-ONLY cycle (login + search + apply, no upload) at:
#        → 11:15 AM,  01:15 PM,  04:15 PM
#
# Daily apply cap is enforced across all 5 runs via apply_count_today.json.
# Tier 1 (Data Engineer) → Tier 2 (Data Analyst) → Tier 3 (Full Stack)
# =============================================================================

WORKSPACE_DIR="/Users/harishankargiri/MyProject/Vibe Coding/Naukri-UpdateResume"
PYTHON="$WORKSPACE_DIR/venv/bin/python"
SCRIPT="$WORKSPACE_DIR/daily_upload.py"
AGENTS_DIR="$HOME/Library/LaunchAgents"

UPLOADER_LABEL="com.harishankargiri.naukri-uploader"
APPLIER_LABEL="com.harishankargiri.naukri-applier"

UPLOADER_PLIST="$AGENTS_DIR/$UPLOADER_LABEL.plist"
APPLIER_PLIST="$AGENTS_DIR/$APPLIER_LABEL.plist"

echo ""
echo "════════════════════════════════════════════════════════"
echo "  Naukri Smart Scheduler Setup"
echo "════════════════════════════════════════════════════════"

# ── unload existing agents if running ────────────────────────────
for LABEL in "$UPLOADER_LABEL" "$APPLIER_LABEL"; do
    if launchctl list | grep -q "$LABEL"; then
        echo "  Unloading existing agent: $LABEL"
        launchctl unload "$AGENTS_DIR/$LABEL.plist" 2>/dev/null
    fi
done

# ── 1. FULL RUN plist (09:15 and 14:00) ──────────────────────────
cat <<EOF > "$UPLOADER_PLIST"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$UPLOADER_LABEL</string>

    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$SCRIPT</string>
        <string>--now</string>
    </array>

    <!-- Run at 09:15 AM and 02:00 PM every day -->
    <key>StartCalendarInterval</key>
    <array>
        <dict>
            <key>Hour</key>   <integer>9</integer>
            <key>Minute</key> <integer>15</integer>
        </dict>
        <dict>
            <key>Hour</key>   <integer>14</integer>
            <key>Minute</key> <integer>0</integer>
        </dict>
    </array>

    <key>WorkingDirectory</key>
    <string>$WORKSPACE_DIR</string>

    <key>StandardOutPath</key>
    <string>$WORKSPACE_DIR/daily_upload_stdout.log</string>

    <key>StandardErrorPath</key>
    <string>$WORKSPACE_DIR/daily_upload_stderr.log</string>

    <!-- Relaunch if it crashes unexpectedly -->
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
EOF

echo "  ✅ Created uploader plist  → 09:15 AM and 02:00 PM (full run)"

# ── 2. APPLY-ONLY plist (11:15, 13:15, 16:15) ────────────────────
cat <<EOF > "$APPLIER_PLIST"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$APPLIER_LABEL</string>

    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$SCRIPT</string>
        <string>--apply-only</string>
    </array>

    <!-- Run at 11:15 AM, 01:15 PM, and 04:15 PM every day -->
    <key>StartCalendarInterval</key>
    <array>
        <dict>
            <key>Hour</key>   <integer>11</integer>
            <key>Minute</key> <integer>15</integer>
        </dict>
        <dict>
            <key>Hour</key>   <integer>13</integer>
            <key>Minute</key> <integer>15</integer>
        </dict>
        <dict>
            <key>Hour</key>   <integer>16</integer>
            <key>Minute</key> <integer>15</integer>
        </dict>
    </array>

    <key>WorkingDirectory</key>
    <string>$WORKSPACE_DIR</string>

    <key>StandardOutPath</key>
    <string>$WORKSPACE_DIR/daily_upload_stdout.log</string>

    <key>StandardErrorPath</key>
    <string>$WORKSPACE_DIR/daily_upload_stderr.log</string>

    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
EOF

echo "  ✅ Created applier plist   → 11:15 AM, 01:15 PM, 04:15 PM (apply-only)"

# ── set permissions ───────────────────────────────────────────────
chmod 644 "$UPLOADER_PLIST"
chmod 644 "$APPLIER_PLIST"

# ── load both agents ──────────────────────────────────────────────
echo ""
echo "  Loading agents into launchd..."
launchctl load "$UPLOADER_PLIST"
launchctl load "$APPLIER_PLIST"

# ── verify ────────────────────────────────────────────────────────
echo ""
UPLOADER_OK=false
APPLIER_OK=false

if launchctl list | grep -q "$UPLOADER_LABEL"; then
    UPLOADER_OK=true
fi
if launchctl list | grep -q "$APPLIER_LABEL"; then
    APPLIER_OK=true
fi

echo "════════════════════════════════════════════════════════"
echo "  Verification"
echo "════════════════════════════════════════════════════════"

if $UPLOADER_OK; then
    echo "  ✅ Uploader agent loaded   (09:15 AM | 02:00 PM)"
else
    echo "  ❌ Uploader agent FAILED to load"
fi

if $APPLIER_OK; then
    echo "  ✅ Applier agent loaded    (11:15 AM | 01:15 PM | 04:15 PM)"
else
    echo "  ❌ Applier agent FAILED to load"
fi

echo ""
echo "  Daily schedule summary:"
echo "    09:15 AM → Full run  (resume upload + headline + apply ≤5)"
echo "    11:15 AM → Apply only (≤5 jobs, respects daily cap of 20)"
echo "    01:15 PM → Apply only (≤5 jobs)"
echo "    02:00 PM → Full run  (resume upload + headline + apply ≤5)"
echo "    04:15 PM → Apply only (remaining daily cap)"
echo ""
echo "  Logs:"
echo "    App log  → $WORKSPACE_DIR/daily_upload.log"
echo "    Stdout   → $WORKSPACE_DIR/daily_upload_stdout.log"
echo "    Stderr   → $WORKSPACE_DIR/daily_upload_stderr.log"
echo ""
echo "  Manual triggers:"
echo "    Full run:    launchctl start $UPLOADER_LABEL"
echo "    Apply only:  launchctl start $APPLIER_LABEL"
echo ""
echo "  To remove the scheduler, run:  bash remove_scheduler.sh"
echo "════════════════════════════════════════════════════════"
