#!/bin/bash

# =============================================================================
# setup_scheduler.sh — Naukri Smart Job Applier + Resume Uploader
#
# Creates THREE separate launchd agents:
#
#   1. com.harishankargiri.naukri-uploader
#      Runs FULL cycle (resume upload + headline rotation + apply) at:
#        → 09:15 AM  and  02:00 PM
#
#   2. com.harishankargiri.naukri-applier
#      Runs APPLY-ONLY cycle (login + search + apply, no upload) at:
#        → 11:15 AM,  01:15 PM,  04:15 PM
#
#   3. com.harishankargiri.naukri-weekly-summary
#      Generates weekly application summary report:
#        → Every Sunday at 6:00 PM
#
# Features:
#   ✦ Cross-day dedup:  applied_jobs_history.json (no double-applies)
#   ✦ Headline rotation: 4 variants cycle each full run
#   ✦ Weekend mode:     job_age expands to 3 days on Sat/Sun
#   ✦ Weekly summary:   auto-generated every Sunday 6 PM
# =============================================================================

WORKSPACE_DIR="/Users/harishankargiri/MyProject/Vibe Coding/Naukri-UpdateResume"
PYTHON="$WORKSPACE_DIR/venv/bin/python"
SCRIPT="$WORKSPACE_DIR/daily_upload.py"
AGENTS_DIR="$HOME/Library/LaunchAgents"

UPLOADER_LABEL="com.harishankargiri.naukri-uploader"
APPLIER_LABEL="com.harishankargiri.naukri-applier"
WEEKLY_LABEL="com.harishankargiri.naukri-weekly-summary"
WATCHER_LABEL="com.harishankargiri.naukri-watcher"

UPLOADER_PLIST="$AGENTS_DIR/$UPLOADER_LABEL.plist"
APPLIER_PLIST="$AGENTS_DIR/$APPLIER_LABEL.plist"
WEEKLY_PLIST="$AGENTS_DIR/$WEEKLY_LABEL.plist"
WATCHER_PLIST="$AGENTS_DIR/$WATCHER_LABEL.plist"

echo ""
echo "════════════════════════════════════════════════════════"
echo "  Naukri Smart Scheduler Setup"
echo "════════════════════════════════════════════════════════"

# ── unload existing agents if running ────────────────────────────
for LABEL in "$UPLOADER_LABEL" "$APPLIER_LABEL" "$WEEKLY_LABEL" "com.harishankargiri.naukri-recommended" "$WATCHER_LABEL"; do
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

# ── 3. WEEKLY SUMMARY plist (Sunday 6:00 PM) ─────────────────────
cat <<EOF > "$WEEKLY_PLIST"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$WEEKLY_LABEL</string>

    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$SCRIPT</string>
        <string>--weekly-summary</string>
    </array>

    <!-- Run every Sunday at 6:00 PM (Weekday 0 = Sunday in launchd) -->
    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key> <integer>0</integer>
        <key>Hour</key>    <integer>18</integer>
        <key>Minute</key>  <integer>0</integer>
    </dict>

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

echo "  ✅ Created weekly summary plist  → Every Sunday at 6:00 PM"

# ── 4. WATCHER CATCH-UP plist (Runs every 15 minutes) ─────────────
cat <<EOF > "$WATCHER_PLIST"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$WATCHER_LABEL</string>

    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$SCRIPT</string>
        <string>--catch-up</string>
    </array>

    <!-- Run every 15 minutes -->
    <key>StartInterval</key>
    <integer>900</integer>

    <!-- Run when network configuration changes (e.g. on wake/connecting to internet) -->
    <key>WatchPaths</key>
    <array>
        <string>/private/var/run/resolv.conf</string>
    </array>

    <!-- Prevent running more than once every 30 seconds if file changes rapidly -->
    <key>ThrottleInterval</key>
    <integer>30</integer>

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

echo "  ✅ Created watcher plist         → Every 15 minutes (catch-up)"

# ── set permissions ───────────────────────────────────────────────
chmod 644 "$UPLOADER_PLIST"
chmod 644 "$APPLIER_PLIST"
chmod 644 "$WEEKLY_PLIST"
chmod 644 "$WATCHER_PLIST"

# ── load all agents ───────────────────────────────────────────────
echo ""
echo "  Loading agents into launchd..."
launchctl load "$UPLOADER_PLIST"
launchctl load "$APPLIER_PLIST"
launchctl load "$WEEKLY_PLIST"
launchctl load "$WATCHER_PLIST"

# ── verify ────────────────────────────────────────────────────────
echo ""
UPLOADER_OK=false
APPLIER_OK=false
WEEKLY_OK=false
WATCHER_OK=false

if launchctl list | grep -q "$UPLOADER_LABEL"; then UPLOADER_OK=true; fi
if launchctl list | grep -q "$APPLIER_LABEL";  then APPLIER_OK=true;  fi
if launchctl list | grep -q "$WEEKLY_LABEL";   then WEEKLY_OK=true;   fi
if launchctl list | grep -q "$WATCHER_LABEL";  then WATCHER_OK=true;  fi

echo "════════════════════════════════════════════════════════"
echo "  Verification"
echo "════════════════════════════════════════════════════════"

if $UPLOADER_OK; then
    echo "  ✅ Uploader agent loaded        (09:15 AM | 02:00 PM)"
else
    echo "  ❌ Uploader agent FAILED to load"
fi

if $APPLIER_OK; then
    echo "  ✅ Applier agent loaded         (11:15 AM | 01:15 PM | 04:15 PM)"
else
    echo "  ❌ Applier agent FAILED to load"
fi

if $WEEKLY_OK; then
    echo "  ✅ Weekly summary agent loaded  (Every Sunday 6:00 PM)"
else
    echo "  ❌ Weekly summary agent FAILED to load"
fi

if $WATCHER_OK; then
    echo "  ✅ Watcher agent loaded         (Every 15 minutes)"
else
    echo "  ❌ Watcher agent FAILED to load"
fi

echo ""
echo "  Daily schedule summary:"
echo "    Every 15 mins   → Catch-up check (runs missed runs automatically)"
echo "    09:15 AM        → Full run  (resume upload + headline rotation + apply ≤5 + recommended)"
echo "    11:15 AM        → Apply only (≤5 jobs + recommended, respects daily cap of 20)"
echo "    01:15 PM        → Apply only (≤5 jobs + recommended)"
echo "    02:00 PM        → Full run  (resume upload + headline rotation + apply ≤5 + recommended)"
echo "    04:15 PM        → Apply only (remaining daily cap + recommended)"
echo "    Sunday 6:00 PM  → Weekly summary report generated"
echo ""
echo "  New features active:"
echo "    ✦ Cross-day dedup:     applied_jobs_history.json prevents re-applying to same job"
echo "    ✦ Headline rotation:   4 variants cycle on each full run"
echo "    ✦ Weekend mode:        job_age expands to 3 days on Sat/Sun"
echo "    ✦ Weekly summary:      auto-generated every Sunday 6 PM"
echo "    ✦ Catch-up watcher:    runs missed scheduled runs when coming online"
echo ""
echo "  Logs:"
echo "    App log  → $WORKSPACE_DIR/daily_upload.log"
echo "    Stdout   → $WORKSPACE_DIR/daily_upload_stdout.log"
echo "    Stderr   → $WORKSPACE_DIR/daily_upload_stderr.log"
echo ""
echo "  Manual triggers:"
echo "    Full run:        launchctl start $UPLOADER_LABEL"
echo "    Apply only:      launchctl start $APPLIER_LABEL"
echo "    Weekly summary:  launchctl start $WEEKLY_LABEL"
echo "    Watcher check:   launchctl start $WATCHER_LABEL"
echo "    (Standalone)     python daily_upload.py --weekly-summary"
echo ""
echo "  To remove all schedulers, run:  bash remove_scheduler.sh"
echo "════════════════════════════════════════════════════════"
