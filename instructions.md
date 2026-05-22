# Naukri Auto-Pilot Automation Guide

## How the Automation Works (In Simple Words)

1. **Automatic Login**: The script logs into your Naukri.com account securely using the credentials stored in your local `.env` file.
2. **Profile Refresh (Full Run Only)**: It downloads your resume, uploads it again to refresh Naukri's "last updated" timestamp, and updates your profile headline slightly (e.g., adding or removing a trailing space/pipe depending on the time of day). This keeps your profile fresh and highly visible to recruiters.
3. **Smart Job Application**: It searches for jobs using a prioritized tiered structure:
   - **Tier 1**: Data Engineer / Associate Data Engineer
   - **Tier 2**: Data Analyst / Backend Python/Node Engineer
   - **Tier 3**: Full Stack Developer
   It searches Tier 1 first, and only moves to Tier 2/3 if it runs out of matches.
4. **Session & Daily Caps**: It reads and writes to `apply_count_today.json` to keep track of how many jobs have been applied to today, ensuring you never exceed your configured daily cap (default: `20` applications/day) or session cap (default: `5` applications/run). This mimics organic human activity and respects Naukri limits.

---

## Running it Manually

Run these commands in your macOS Terminal:

```bash
# Navigate to the project directory
cd "/Users/harishankargiri/MyProject/Vibe Coding/Naukri-UpdateResume"

# Run a Full Cycle (Resume upload, Headline update, and Apply)
venv/bin/python daily_upload.py --now

# Run an Apply-Only Cycle (Job search and Apply only)
venv/bin/python daily_upload.py --apply-only
```

*Note: Alternatively, if you activate your virtual environment (`source venv/bin/activate`), you can run them using simple `python daily_upload.py --now` or `python daily_upload.py --apply-only`.*

---

## Viewing Scheduled Jobs on Your Mac (Launchd)

On macOS, background tasks are scheduled using Launchd (managed via `.plist` files) rather than standard Unix cron. Run the following commands to check them:

```bash
# List active Naukri launchd jobs
launchctl list | grep naukri

# Inspect the scheduled configuration files
cat ~/Library/LaunchAgents/com.harishankargiri.naukri-uploader.plist
cat ~/Library/LaunchAgents/com.harishankargiri.naukri-applier.plist

# Check standard user crontab logs (if any are set up)
crontab -l
```

### Manual Triggers via Launchd
You can also manually trigger the scheduled agents instantly in the background:
```bash
# Trigger Full Cycle Agent
launchctl start com.harishankargiri.naukri-uploader

# Trigger Apply-Only Agent
launchctl start com.harishankargiri.naukri-applier
```

---

## Checking Logs
To monitor what the automation is doing, check the output log files in the project folder:

```bash
# View combined logs
tail -f daily_upload.log

# View standard output logs
tail -f daily_upload_stdout.log

# View standard error logs
tail -f daily_upload_stderr.log
```
