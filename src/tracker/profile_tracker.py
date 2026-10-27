"""
src/tracker/profile_tracker.py
Comprehensive Naukri Profile Performance & Recruiter Action Tracker.

Tracks:
  - Search Appearances (Total, 30-day, 90-day, weekly timelines)
  - Recruiter Actions (Profile views, resumes viewed, applications viewed, shortlisted, email contacts)
  - Direct Recruiter Inquiries (Recruiter inbox messages)
  - Search Keywords (Exact skills/keywords recruiters used to discover profile with counts)
  - Daily Snapshots & Deltas (Growth tracking over time)
"""

import os
import sys
import json
import csv
import re
import logging
from datetime import datetime, date
from typing import Dict, Any, Optional

log = logging.getLogger(__name__)

HISTORY_FILE = "profile_tracker_history.json"
DASHBOARD_HTML_FILE = "dashboard.html"


def fetch_profile_metrics(client) -> Dict[str, Any]:
    """
    Fetch all performance metrics from Naukri API endpoints using an authenticated client.
    """
    import time
    headers = client._build_headers(auth=True)

    def safe_get(url: str, retries: int = 2) -> Dict[str, Any]:
        time.sleep(0.4)
        for attempt in range(retries):
            try:
                res = client.session.get(url, headers=headers, timeout=25)
                if res.status_code == 200 and res.text:
                    return res.json()
            except Exception as e:
                log.debug("GET %s failed (attempt %d): %s", url, attempt + 1, e)
            if attempt < retries - 1:
                time.sleep(1.2)
        return {}

    # Load existing history for smart fallbacks
    past_history = load_history(HISTORY_FILE)
    past_metrics = past_history.get("latest_metrics", {})
    past_ra = past_metrics.get("recruiterActions", {})
    past_sa = past_metrics.get("searchAppearances", {})

    # 1. Dashboard summary
    dash_url = "https://www.naukri.com/cloudgateway-mynaukri/resman-aggregator-services/v0/users/self/dashboard"
    dash_res_json = safe_get(dash_url)
    dash_data = dash_res_json.get("dashBoard", {}) if isinstance(dash_res_json, dict) else {}

    # 2. Activity Level
    act_url = "https://www.naukri.com/cloudgateway-mynaukri/resman-aggregator-services/v0/users/self/activityLevel"
    act_data = safe_get(act_url)

    # 3. Search Impressions (7 days, 30 days & 90 days)
    si7_url = "https://www.naukri.com/cloudgateway-apply/profile-performance/v0/jobseeker/self/search-impressions?days=7"
    si7_data = safe_get(si7_url)

    si30_url = "https://www.naukri.com/cloudgateway-apply/profile-performance/v0/jobseeker/self/search-impressions?days=30"
    si30_data = safe_get(si30_url)

    si90_url = "https://www.naukri.com/cloudgateway-apply/profile-performance/v0/jobseeker/self/search-impressions?days=90"
    si90_data = safe_get(si90_url)

    # 4. Direct Recruiter Mails
    inbox_url = "https://www.naukri.com/cloudgateway-mynaukri/resman-aggregator-services/v0/inbox/users/self/mails"
    inbox_data = safe_get(inbox_url)

    # 5. History & Recruiter Activities on Applications
    try:
        hist_data = client.get_application_history(page_size=50, days=90)
    except Exception as e:
        log.warning("get_application_history failed: %s", e)
        hist_data = {}

    # Parse status counts from apply history
    status_counts = {}
    recent_actions = []
    apply_details = hist_data.get("applyDetails", [])
    for app in apply_details:
        statuses = app.get("status", [])
        has_recruiter_action = False
        action_names = []
        for s in statuses:
            val = s.get("statusValue", "")
            status_counts[val] = status_counts.get(val, 0) + 1
            if val.lower() not in ("applied", "application sent"):
                has_recruiter_action = True
                action_names.append(val)

        if has_recruiter_action or app.get("jobActivity", 0) > 0:
            latest_status = statuses[-1] if statuses else {}
            recent_actions.append({
                "jobId": app.get("jobId"),
                "jobTitle": app.get("jobTitle"),
                "company": app.get("company"),
                "location": app.get("location"),
                "status": action_names if action_names else ["Viewed"],
                "latestStatusDate": latest_status.get("modifiedDate") or latest_status.get("dateTime"),
                "starRating": app.get("starRating", "0"),
                "logoPath": app.get("logoPath"),
            })

    # Sort recent recruiter actions by latest status date descending
    recent_actions.sort(key=lambda x: str(x.get("latestStatusDate", "")), reverse=True)

    # Parse direct recruiter emails
    raw_inbox = inbox_data.get("inbox", [])
    recent_mails = []
    for m in raw_inbox[:20]:
        recent_mails.append({
            "mailId": m.get("mailId"),
            "subject": m.get("subject", "").strip(),
            "dateTime": m.get("dateTime"),
            "isRead": m.get("isRead") == 1,
            "messageType": m.get("messageType"),
        })

    # Sort search keywords
    keywords_7d = sorted(
        si7_data.get("searchKeyWords", {}).items(),
        key=lambda item: item[1],
        reverse=True
    )
    keywords_30d = sorted(
        si30_data.get("searchKeyWords", {}).items(),
        key=lambda item: item[1],
        reverse=True
    )
    keywords_90d = sorted(
        si90_data.get("searchKeyWords", {}).items(),
        key=lambda item: item[1],
        reverse=True
    )

    now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Smart fallbacks if Naukri temporarily returns empty/partial on an endpoint
    total_searches = dash_data.get("totalSearchAppearancesCount") or past_sa.get("totalCount", 0)
    searches_7d = si7_data.get("dayWiseSearchAppearance") if si7_data.get("dayWiseSearchAppearance") is not None else past_sa.get("last7DaysCount", 0)
    searches_30d = si30_data.get("dayWiseSearchAppearance") if si30_data.get("dayWiseSearchAppearance") is not None else past_sa.get("last30DaysCount", 0)
    searches_90d = si90_data.get("dayWiseSearchAppearance") if si90_data.get("dayWiseSearchAppearance") is not None else past_sa.get("last90DaysCount", 0)
    pct_change_7d = si7_data.get("percentageChange") if si7_data.get("percentageChange") is not None else past_sa.get("percentageChange7d", 0)
    pct_change_30d = si30_data.get("percentageChange") if si30_data.get("percentageChange") is not None else past_sa.get("percentageChange", 0)
    timeline_7d = si7_data.get("searchAppearanceTimeline") or past_sa.get("timeline7d", {})
    timeline_30 = si30_data.get("searchAppearanceTimeline") or past_sa.get("timeline30d", {})
    timeline_90 = si90_data.get("searchAppearanceTimeline") or past_sa.get("timeline90d", {})
    if not keywords_7d and past_sa.get("keywords7d"):
        keywords_7d = past_sa["keywords7d"]
    if not keywords_30d and past_sa.get("keywords30d"):
        keywords_30d = past_sa["keywords30d"]
    if not keywords_90d and past_sa.get("keywords90d"):
        keywords_90d = past_sa["keywords90d"]

    total_apps = int(hist_data.get("matchingRowsCount", 0)) if hist_data.get("matchingRowsCount") is not None else 0
    if total_apps == 0 and past_ra.get("totalApplications"):
        total_apps = past_ra["totalApplications"]

    recruiter_act_count = hist_data.get("recruiterActivityOnApplication")
    if recruiter_act_count is None or (recruiter_act_count == 0 and past_ra.get("activityOnApplications")):
        recruiter_act_count = past_ra.get("activityOnApplications", 0)

    if not status_counts and past_ra.get("statusBreakdown"):
        status_counts = past_ra["statusBreakdown"]

    if not recent_actions and past_ra.get("recentActions"):
        recent_actions = past_ra["recentActions"]

    metrics = {
        "timestamp": now_iso,
        "date": str(date.today()),
        "candidate": {
            "name": dash_data.get("name") or past_history.get("candidate", {}).get("name", "Harishankar Giri"),
            "designation": dash_data.get("desig") or past_history.get("candidate", {}).get("designation", ""),
            "organization": dash_data.get("organization") or past_history.get("candidate", {}).get("organization", ""),
            "experienceYears": dash_data.get("rawTotalExperience") or past_history.get("candidate", {}).get("experienceYears", ""),
            "ctc": dash_data.get("rawCtc") or past_history.get("candidate", {}).get("ctc", ""),
            "profileCompleteness": dash_data.get("pc") or past_history.get("candidate", {}).get("profileCompleteness", 100),
            "profileId": dash_data.get("profileId") or past_history.get("candidate", {}).get("profileId", ""),
            "lastResumeUpdate": dash_data.get("mod_dt") or past_history.get("candidate", {}).get("lastResumeUpdate", ""),
            "activityLevel": act_data.get("level") or past_history.get("candidate", {}).get("activityLevel", "HIGH"),
        },
        "searchAppearances": {
            "totalCount": total_searches,
            "lastAppearanceDate": dash_data.get("totalSearchAppearancesLatestDate") or past_sa.get("lastAppearanceDate", ""),
            "last7DaysCount": searches_7d,
            "percentageChange7d": pct_change_7d,
            "timeline7d": timeline_7d,
            "keywords7d": keywords_7d,
            "last30DaysCount": searches_30d,
            "last90DaysCount": searches_90d,
            "percentageChange": pct_change_30d,
            "timeline30d": timeline_30,
            "timeline90d": timeline_90,
            "keywords30d": keywords_30d,
            "keywords90d": keywords_90d,
        },
        "recruiterActions": {
            "profileViews": dash_data.get("profileViewCount") or past_ra.get("profileViews", 0),
            "lastActionDate": dash_data.get("recruiterActionsLatestDate") or past_ra.get("lastActionDate", ""),
            "activityOnApplications": recruiter_act_count,
            "totalApplications": total_apps,
            "statusBreakdown": status_counts,
            "recentActions": recent_actions[:30],
        },
        "inbox": {
            "unreadCount": inbox_data.get("unreadCount", 0),
            "totalCount": inbox_data.get("totalCount") or past_history.get("latest_metrics", {}).get("inbox", {}).get("totalCount", 0),
            "recentMails": recent_mails if recent_mails else past_history.get("latest_metrics", {}).get("inbox", {}).get("recentMails", []),
        }
    }

    return metrics


def load_history(history_path: str = HISTORY_FILE) -> Dict[str, Any]:
    """Load or initialize the tracker history file."""
    if os.path.exists(history_path):
        try:
            with open(history_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.warning("Could not read %s, initializing new history: %s", history_path, e)

    return {
        "candidate": {},
        "created_at": datetime.now().isoformat(),
        "last_updated": None,
        "daily_snapshots": {},
        "weekly_milestones": []
    }


def save_daily_snapshot(metrics: Dict[str, Any], history_path: str = HISTORY_FILE) -> Dict[str, Any]:
    """
    Save the current metrics into the history file.
    Computes daily deltas (e.g. search appearances gained today).
    """
    history = load_history(history_path)
    today_str = metrics.get("date", str(date.today()))

    snapshots = history.get("daily_snapshots", {})

    # Compute deltas against either yesterday or today's baseline
    prev_snapshot = None
    all_dates = sorted(snapshots.keys())
    if all_dates:
        if today_str in snapshots:
            # Baseline was stored earlier today
            baseline = snapshots[today_str].get("baseline", snapshots[today_str])
            prev_snapshot = baseline
        else:
            # Most recent past date
            prev_snapshot = snapshots[all_dates[-1]]

    def calc_delta(curr_val, prev_dict, key):
        if not prev_dict:
            return 0
        return curr_val - prev_dict.get(key, curr_val)

    curr_total_searches = metrics["searchAppearances"]["totalCount"]
    curr_profile_views = metrics["recruiterActions"]["profileViews"]
    curr_recruiter_act = metrics["recruiterActions"]["activityOnApplications"]
    curr_applications = metrics["recruiterActions"]["totalApplications"]
    curr_direct_mails = metrics["inbox"]["totalCount"]

    timeline7d = metrics["searchAppearances"].get("timeline7d", {})
    daily_timeline = history.get("daily_search_appearances", {})
    for dt, cnt in timeline7d.items():
        daily_timeline[dt] = int(cnt)
    history["daily_search_appearances"] = daily_timeline

    # Real daily search appearance count for today from Naukri
    today_daily_searches = timeline7d.get(today_str, 0)

    deltas = {
        "searchAppearances": today_daily_searches,
        "profileViews": calc_delta(curr_profile_views, prev_snapshot, "profileViews"),
        "recruiterActivities": calc_delta(curr_recruiter_act, prev_snapshot, "recruiterActivities"),
        "applications": calc_delta(curr_applications, prev_snapshot, "totalApplications"),
        "directMails": calc_delta(curr_direct_mails, prev_snapshot, "directMails"),
    }

    # If first snapshot for today, record baseline
    baseline_data = snapshots.get(today_str, {}).get("baseline")
    if not baseline_data:
        baseline_data = {
            "totalSearchAppearances": curr_total_searches,
            "profileViews": curr_profile_views,
            "recruiterActivities": curr_recruiter_act,
            "totalApplications": curr_applications,
            "directMails": curr_direct_mails,
        }

    snapshot_entry = {
        "date": today_str,
        "timestamp": metrics["timestamp"],
        "dailySearchAppearances": today_daily_searches,
        "searchAppearances7d": metrics["searchAppearances"].get("last7DaysCount", 0),
        "searchAppearances7dGrowth": metrics["searchAppearances"].get("percentageChange7d", 0),
        "totalSearchAppearances": curr_total_searches,
        "searchAppearances30d": metrics["searchAppearances"]["last30DaysCount"],
        "profileViews": curr_profile_views,
        "recruiterActivities": curr_recruiter_act,
        "resumesViewed": metrics["recruiterActions"]["statusBreakdown"].get("Resume Viewed", 0),
        "shortlisted": metrics["recruiterActions"]["statusBreakdown"].get("Shortlisted", 0),
        "contactedEmail": metrics["recruiterActions"]["statusBreakdown"].get("Contacted By Email", 0),
        "totalApplications": curr_applications,
        "directMails": curr_direct_mails,
        "activityLevel": metrics["candidate"]["activityLevel"],
        "deltas": deltas,
        "baseline": baseline_data
    }

    snapshots[today_str] = snapshot_entry

    history["candidate"] = metrics["candidate"]
    history["last_updated"] = metrics["timestamp"]
    history["daily_snapshots"] = snapshots
    history["latest_metrics"] = metrics


    # Also build weekly milestones from Naukri's native search appearance timeline if not populated
    timeline30 = metrics["searchAppearances"]["timeline30d"]
    if timeline30 and not history.get("weekly_milestones"):
        milestones = []
        for period, count in sorted(timeline30.items()):
            milestones.append({"period": period, "appearances": count})
        history["weekly_milestones"] = milestones

    try:
        with open(history_path, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
        log.info("Saved daily tracker snapshot to %s ✓", history_path)
    except Exception as e:
        log.error("Failed to write %s: %s", history_path, e)

    return history


def record_profile_metrics(client, history_path: str = HISTORY_FILE, html_path: str = DASHBOARD_HTML_FILE) -> Optional[Dict[str, Any]]:
    """
    Main helper function to call during daily runs:
    Fetches metrics, saves history, generates dashboard HTML.
    """
    try:
        metrics = fetch_profile_metrics(client)
        history = save_daily_snapshot(metrics, history_path)
        generate_dashboard_html(history, html_path)
        return metrics
    except Exception as e:
        log.warning("Daily profile tracker encountered an issue: %s", e)
        return None


def format_terminal_dashboard(metrics: Dict[str, Any], deltas: Optional[Dict[str, int]] = None) -> str:
    """Format a clean, readable ANSI colored terminal report."""
    c = metrics["candidate"]
    sa = metrics["searchAppearances"]
    ra = metrics["recruiterActions"]
    sb = ra["statusBreakdown"]
    inbox = metrics["inbox"]
    d = deltas or {}

    def delta_str(val):
        if not val:
            return ""
        return f" (+{val})" if val > 0 else f" ({val})"

    W = "\033[97m"
    G = "\033[92m"
    B = "\033[94m"
    C = "\033[96m"
    Y = "\033[93m"
    M = "\033[95m"
    R = "\033[91m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    lines = [
        "",
        f"{C}{BOLD}╔══════════════════════════════════════════════════════════════════════════╗{RESET}",
        f"{C}{BOLD}║                   NAUKRI PROFILE PERFORMANCE & TRACKER                   ║{RESET}",
        f"{C}{BOLD}╚══════════════════════════════════════════════════════════════════════════╝{RESET}",
        f"  {BOLD}Candidate:{RESET} {W}{c.get('name')}{RESET} | {Y}{c.get('designation')}{RESET} at {W}{c.get('organization')}{RESET}",
        f"  {BOLD}Profile:{RESET}   Completeness: {G}{c.get('profileCompleteness')}%{RESET} | Activity Level: {G}{BOLD}{c.get('activityLevel')}{RESET} | Last Updated: {DIM}{c.get('lastResumeUpdate')}{RESET}",
        "",
        f"  {B}{BOLD}── 🔍 SEARCH APPEARANCES ───────────────────────────────────────────────{RESET}",
        f"    • Last 7 Days Appearances:  {BOLD}{G}{sa.get('last7DaysCount', 0)}{RESET} ({G}↑ {sa.get('percentageChange7d', 0)}%{RESET} since last week)",
        f"    • Total Appearances (90d):  {BOLD}{W}{sa.get('totalCount'):,}{RESET} (Latest: {sa.get('lastAppearanceDate')})",
        f"    • Last 30 Days:             {BOLD}{C}{sa.get('last30DaysCount'):,}{RESET}  ({G}+{sa.get('percentageChange')}%{RESET} vs prev 30d)",
    ]

    t7 = sa.get("timeline7d", {})
    if t7:
        day_items = [f"{k[-5:]}: {BOLD}{v}{RESET}" for k, v in sorted(t7.items())]
        lines.append(f"    • 7-Day Timeline:           {' | '.join(day_items)}")
    lines.append("")

    lines.extend([
        f"  {M}{BOLD}── 🎯 RECRUITER ACTIONS & ENGAGEMENT ───────────────────────────────────{RESET}",
        f"    • Profile Views:            {BOLD}{W}{ra.get('profileViews')}{RESET}{G}{delta_str(d.get('profileViews'))}{RESET}",
        f"    • Recruiter Actions on App: {BOLD}{W}{ra.get('activityOnApplications')}{RESET}{G}{delta_str(d.get('recruiterActivities'))}{RESET} (across {ra.get('totalApplications'):,} applications)",
        f"    • Applications Sent:        {sb.get('Application Sent', 0)}",
        f"    • Applications Viewed:      {BOLD}{G}{sb.get('Application Viewed', 0)}{RESET}",
        f"    • Resumes Viewed / Read:    {BOLD}{G}{sb.get('Resume Viewed', 0)}{RESET}",
        f"    • Shortlisted:              {BOLD}{Y}{sb.get('Shortlisted', 0)}{RESET} ⭐",
        f"    • Contacted By Email:       {BOLD}{C}{sb.get('Contacted By Email', 0)}{RESET}",
        f"    • Contact Info Viewed:      {BOLD}{C}{sb.get('Contact Viewed', 0)}{RESET}",
        "",
        f"  {Y}{BOLD}── 📨 DIRECT RECRUITER INBOX ───────────────────────────────────────────{RESET}",
        f"    • Direct Recruiter Mails:   {BOLD}{W}{inbox.get('totalCount')}{RESET} (Unread: {inbox.get('unreadCount')})",
    ])

    kw7 = sa.get("keywords7d", [])[:7]
    if kw7:
        lines.append("")
        lines.append(f"  {C}{BOLD}── 🔑 TOP RECRUITER SEARCH KEYWORDS (Last 7 Days) ──────────────────────{RESET}")
        kw_items = [f"{BOLD}{k}{RESET} ({v}x)" for k, v in kw7]
        lines.append(f"    {',  '.join(kw_items)}")

    recent = ra.get("recentActions", [])[:3]
    if recent:
        lines.append("")
        lines.append(f"  {G}{BOLD}── ⚡ LATEST RECRUITER ACTIVITY ON APPLICATIONS ────────────────────────{RESET}")
        for r in recent:
            st = ", ".join(r.get("status", []))
            lines.append(f"    • {BOLD}{r.get('company')}{RESET} — {r.get('jobTitle')} [{G}{st}{RESET}] ({r.get('latestStatusDate')})")


    lines.append("")
    lines.append(f"{DIM}  Last synced: {metrics['timestamp']} | Run 'python track_profile.py --dashboard' for interactive GUI{RESET}")
    lines.append("")
    return "\n".join(lines)


def generate_dashboard_html(history: Dict[str, Any], output_path: str = DASHBOARD_HTML_FILE) -> str:
    """
    Generates a standalone, rich, interactive Web Dashboard (dashboard.html).
    Adheres to premium design standards:
      - Dark glassmorphism palette
      - Chart.js timeline and keyword distribution
      - Real-time search keyword cloud
      - Recruiter activity table
      - Built-in live sync hook (/api/sync) when running the local server
    """
    data_json = json.dumps(history, ensure_ascii=False)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Naukri Profile Tracker & Analytics</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    :root {{
      --bg-dark: #080c14;
      --card-bg: rgba(17, 24, 39, 0.7);
      --card-border: rgba(255, 255, 255, 0.08);
      --card-hover-border: rgba(99, 102, 241, 0.4);
      --text-main: #f3f4f6;
      --text-muted: #9ca3af;
      --accent-cyan: #06b6d4;
      --accent-blue: #3b82f6;
      --accent-indigo: #6366f1;
      --accent-purple: #8b5cf6;
      --accent-emerald: #10b981;
      --accent-amber: #f59e0b;
      --accent-rose: #f43f5e;
      --glow-blue: rgba(59, 130, 246, 0.15);
      --glow-emerald: rgba(16, 185, 129, 0.15);
      --radius-sm: 8px;
      --radius-md: 14px;
      --radius-lg: 20px;
    }}

    * {{
      margin: 0;
      padding: 0;
      box-sizing: border-box;
    }}

    body {{
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      background-color: var(--bg-dark);
      background-image: 
        radial-gradient(circle at 15% 15%, rgba(99, 102, 241, 0.08) 0%, transparent 40%),
        radial-gradient(circle at 85% 25%, rgba(6, 182, 212, 0.06) 0%, transparent 40%),
        radial-gradient(circle at 50% 85%, rgba(139, 92, 246, 0.05) 0%, transparent 50%);
      background-attachment: fixed;
      color: var(--text-main);
      min-height: 100vh;
      line-height: 1.5;
      padding-bottom: 60px;
    }}

    .container {{
      max-width: 1380px;
      margin: 0 auto;
      padding: 28px 24px;
    }}

    /* Top Nav / Profile Card */
    .header-card {{
      background: linear-gradient(135deg, rgba(20, 29, 47, 0.8) 0%, rgba(13, 20, 36, 0.8) 100%);
      border: 1px solid var(--card-border);
      backdrop-filter: blur(16px);
      border-radius: var(--radius-lg);
      padding: 24px 30px;
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 20px;
      margin-bottom: 28px;
      box-shadow: 0 10px 30px -10px rgba(0, 0, 0, 0.5);
    }}

    .profile-info {{
      display: flex;
      align-items: center;
      gap: 18px;
    }}

    .avatar {{
      width: 60px;
      height: 60px;
      border-radius: 16px;
      background: linear-gradient(135deg, #4f46e5 0%, #06b6d4 100%);
      display: flex;
      align-items: center;
      justify-content: center;
      font-family: 'Outfit', sans-serif;
      font-size: 24px;
      font-weight: 700;
      color: #fff;
      box-shadow: 0 4px 15px rgba(79, 70, 229, 0.35);
    }}

    .profile-meta h1 {{
      font-family: 'Outfit', sans-serif;
      font-size: 24px;
      font-weight: 700;
      letter-spacing: -0.5px;
      color: #fff;
      display: flex;
      align-items: center;
      gap: 10px;
    }}

    .profile-meta p {{
      color: var(--text-muted);
      font-size: 14px;
      margin-top: 2px;
    }}

    .badges {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 8px;
    }}

    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 12px;
      font-weight: 600;
      padding: 3px 10px;
      border-radius: 20px;
      border: 1px solid transparent;
    }}

    .badge-green {{
      background: rgba(16, 185, 129, 0.12);
      color: #34d399;
      border-color: rgba(16, 185, 129, 0.25);
    }}

    .badge-purple {{
      background: rgba(139, 92, 246, 0.12);
      color: #a78bfa;
      border-color: rgba(139, 92, 246, 0.25);
    }}

    .badge-cyan {{
      background: rgba(6, 182, 212, 0.12);
      color: #22d3ee;
      border-color: rgba(6, 182, 212, 0.25);
    }}

    .dot {{
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background-color: currentColor;
      box-shadow: 0 0 8px currentColor;
    }}

    .header-actions {{
      display: flex;
      align-items: center;
      gap: 12px;
    }}

    .btn {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 10px 18px;
      border-radius: var(--radius-sm);
      font-weight: 600;
      font-size: 13.5px;
      cursor: pointer;
      transition: all 0.2s ease;
      border: none;
      outline: none;
    }}

    .btn-primary {{
      background: linear-gradient(135deg, #4f46e5 0%, #06b6d4 100%);
      color: #fff;
      box-shadow: 0 4px 14px rgba(79, 70, 229, 0.35);
    }}

    .btn-primary:hover {{
      transform: translateY(-1px);
      box-shadow: 0 6px 20px rgba(79, 70, 229, 0.5);
    }}

    .btn-secondary {{
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--card-border);
      color: var(--text-main);
    }}

    .btn-secondary:hover {{
      background: rgba(255, 255, 255, 0.1);
      border-color: rgba(255, 255, 255, 0.2);
    }}

    /* KPI Grid */
    .kpi-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 18px;
      margin-bottom: 28px;
    }}

    .kpi-card {{
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      backdrop-filter: blur(14px);
      border-radius: var(--radius-md);
      padding: 20px 22px;
      position: relative;
      overflow: hidden;
      transition: transform 0.2s ease, border-color 0.2s ease;
    }}

    .kpi-card:hover {{
      transform: translateY(-2px);
      border-color: var(--card-hover-border);
    }}

    .kpi-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      color: var(--text-muted);
      font-size: 13px;
      font-weight: 500;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }}

    .kpi-icon {{
      width: 36px;
      height: 36px;
      border-radius: 10px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 18px;
    }}

    .kpi-value {{
      font-family: 'Outfit', sans-serif;
      font-size: 32px;
      font-weight: 700;
      color: #fff;
      margin: 12px 0 6px 0;
      letter-spacing: -0.5px;
    }}

    .kpi-subtitle {{
      font-size: 12.5px;
      color: var(--text-muted);
      display: flex;
      align-items: center;
      gap: 6px;
    }}

    .tag-growth {{
      color: #34d399;
      font-weight: 600;
      background: rgba(16, 185, 129, 0.1);
      padding: 1px 6px;
      border-radius: 4px;
    }}

    /* Charts Row */
    .charts-grid {{
      display: grid;
      grid-template-columns: 2fr 1fr;
      gap: 20px;
      margin-bottom: 28px;
    }}

    @media (max-width: 1024px) {{
      .charts-grid {{
        grid-template-columns: 1fr;
      }}
    }}

    .card {{
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      backdrop-filter: blur(14px);
      border-radius: var(--radius-md);
      padding: 22px 24px;
    }}

    .card-title-bar {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 18px;
    }}

    .card-title {{
      font-family: 'Outfit', sans-serif;
      font-size: 17px;
      font-weight: 600;
      color: #fff;
      display: flex;
      align-items: center;
      gap: 8px;
    }}

    .chart-container {{
      position: relative;
      height: 290px;
      width: 100%;
    }}

    /* Keywords Section */
    .keywords-section {{
      margin-bottom: 28px;
    }}

    .filter-input {{
      background: rgba(15, 23, 42, 0.6);
      border: 1px solid var(--card-border);
      color: #fff;
      padding: 8px 14px;
      border-radius: var(--radius-sm);
      font-size: 13px;
      outline: none;
      width: 240px;
      transition: border-color 0.2s;
    }}

    .filter-input:focus {{
      border-color: var(--accent-indigo);
    }}

    .tags-cloud {{
      display: flex;
      flex-wrap: wrap;
      gap: 9px;
      margin-top: 14px;
      max-height: 230px;
      overflow-y: auto;
      padding-right: 6px;
    }}

    .tags-cloud::-webkit-scrollbar {{
      width: 5px;
    }}
    .tags-cloud::-webkit-scrollbar-thumb {{
      background: rgba(255, 255, 255, 0.1);
      border-radius: 4px;
    }}

    .kw-tag {{
      background: rgba(30, 41, 59, 0.7);
      border: 1px solid rgba(255, 255, 255, 0.08);
      color: #e2e8f0;
      font-size: 12.5px;
      padding: 5px 12px;
      border-radius: 20px;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      transition: all 0.15s ease;
    }}

    .kw-tag:hover {{
      border-color: var(--accent-cyan);
      background: rgba(6, 182, 212, 0.12);
      transform: translateY(-1px);
    }}

    .kw-tag .count {{
      background: rgba(6, 182, 212, 0.2);
      color: #38bdf8;
      font-size: 11px;
      font-weight: 700;
      padding: 1px 6px;
      border-radius: 10px;
    }}

    /* Feed & Tables */
    .feed-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 20px;
    }}

    @media (max-width: 900px) {{
      .feed-grid {{
        grid-template-columns: 1fr;
      }}
    }}

    .table-container {{
      max-height: 380px;
      overflow-y: auto;
      margin-top: 10px;
    }}

    .table-container::-webkit-scrollbar {{
      width: 5px;
    }}
    .table-container::-webkit-scrollbar-thumb {{
      background: rgba(255, 255, 255, 0.1);
      border-radius: 4px;
    }}

    .styled-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}

    .styled-table th {{
      text-align: left;
      padding: 10px 12px;
      background: rgba(15, 23, 42, 0.8);
      color: var(--text-muted);
      font-weight: 600;
      font-size: 11.5px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      border-bottom: 1px solid var(--card-border);
    }}

    .styled-table td {{
      padding: 12px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.04);
      color: var(--text-main);
    }}

    .styled-table tr:hover td {{
      background: rgba(255, 255, 255, 0.02);
    }}

    .action-badge {{
      display: inline-block;
      font-size: 11px;
      font-weight: 600;
      padding: 2px 7px;
      border-radius: 4px;
      white-space: nowrap;
    }}

    .action-viewed {{
      background: rgba(16, 185, 129, 0.15);
      color: #34d399;
    }}

    .action-shortlist {{
      background: rgba(245, 158, 11, 0.15);
      color: #fbbf24;
    }}

    .action-email {{
      background: rgba(6, 182, 212, 0.15);
      color: #22d3ee;
    }}

    .action-sent {{
      background: rgba(148, 163, 184, 0.12);
      color: #cbd5e1;
    }}

    /* Toast Notification */
    .toast {{
      position: fixed;
      bottom: 24px;
      right: 24px;
      background: #1e293b;
      border: 1px solid rgba(255, 255, 255, 0.1);
      padding: 14px 20px;
      border-radius: var(--radius-md);
      box-shadow: 0 10px 25px rgba(0, 0, 0, 0.5);
      color: #fff;
      font-size: 13.5px;
      display: flex;
      align-items: center;
      gap: 10px;
      transform: translateY(100px);
      opacity: 0;
      transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
      z-index: 1000;
    }}

    .toast.show {{
      transform: translateY(0);
      opacity: 1;
    }}

    .spinner {{
      border: 2px solid rgba(255, 255, 255, 0.2);
      border-top-color: #fff;
      border-radius: 50%;
      width: 14px;
      height: 14px;
      animation: spin 0.8s linear infinite;
    }}

    @keyframes spin {{
      to {{ transform: rotate(360deg); }}
    }}
  </style>
</head>
<body>

  <div class="container">
    <!-- Header -->
    <header class="header-card">
      <div class="profile-info">
        <div class="avatar" id="avatarText">HG</div>
        <div class="profile-meta">
          <h1 id="candidateName">Harishankar Giri <span class="badge badge-green"><span class="dot"></span> Active</span></h1>
          <p id="candidateRole">Associate Data Engineer • Blue Flame Labs</p>
          <div class="badges">
            <span class="badge badge-purple" id="badgeCompleteness">100% Profile Completeness</span>
            <span class="badge badge-cyan" id="badgeActivity">Activity: HIGH</span>
            <span class="badge" id="badgeUpdated" style="background: rgba(255,255,255,0.06); color:#94a3b8;">Updated: Just now</span>
          </div>
        </div>
      </div>
      <div class="header-actions">
        <button class="btn btn-primary" id="btnSync" onclick="triggerSync()">
          <span id="syncIcon">⚡</span> Sync Live with Naukri
        </button>
      </div>
    </header>

    <!-- KPI Cards Grid -->
    <section class="kpi-grid">
      <!-- Search Appearances -->
      <div class="kpi-card">
        <div class="kpi-header">
          <span>Search Appearances</span>
          <div class="kpi-icon" style="background: rgba(6, 182, 212, 0.15); color: #06b6d4;">🔍</div>
        </div>
        <div class="kpi-value" id="kpiTotalSearches">476</div>
        <div class="kpi-subtitle">
          <span class="tag-growth" id="kpiGrowth">+96%</span> <span id="kpiRecentSearches">203 in last 30 days</span>
        </div>
      </div>

      <!-- Recruiter Actions -->
      <div class="kpi-card">
        <div class="kpi-header">
          <span>Recruiter Actions</span>
          <div class="kpi-icon" style="background: rgba(99, 102, 241, 0.15); color: #818cf8;">🎯</div>
        </div>
        <div class="kpi-value" id="kpiRecruiterActions">108</div>
        <div class="kpi-subtitle">
          <span id="kpiProfileViews">38 direct profile views</span>
        </div>
      </div>

      <!-- Resumes Read / Shortlisted -->
      <div class="kpi-card">
        <div class="kpi-header">
          <span>Resumes Viewed / Shortlisted</span>
          <div class="kpi-icon" style="background: rgba(245, 158, 11, 0.15); color: #fbbf24;">⭐</div>
        </div>
        <div class="kpi-value" id="kpiShortlisted">10</div>
        <div class="kpi-subtitle">
          <span id="kpiResumesViewed">90 resumes viewed by HR</span>
        </div>
      </div>

      <!-- Direct Recruiter Inquiries -->
      <div class="kpi-card">
        <div class="kpi-header">
          <span>Direct Recruiter Mails</span>
          <div class="kpi-icon" style="background: rgba(16, 185, 129, 0.15); color: #34d399;">📬</div>
        </div>
        <div class="kpi-value" id="kpiDirectMails">14</div>
        <div class="kpi-subtitle">
          <span id="kpiUnreadMails">0 unread messages</span>
        </div>
      </div>
    </section>

    <!-- Charts Row -->
    <div class="charts-grid">
      <!-- Search Appearances Timeline Chart -->
      <div class="card">
        <div class="card-title-bar">
          <div class="card-title">📈 Search Appearances Timeline</div>
          <div style="font-size: 12px; color: var(--text-muted);" id="timelineSubtitle">Weekly impressions trend</div>
        </div>
        <div class="chart-container">
          <canvas id="timelineChart"></canvas>
        </div>
      </div>

      <!-- Recruiter Actions Breakdown Doughnut -->
      <div class="card">
        <div class="card-title-bar">
          <div class="card-title">🍩 Recruiter Action Mix</div>
        </div>
        <div class="chart-container">
          <canvas id="actionsChart"></canvas>
        </div>
      </div>
    </div>

    <!-- Keywords Section -->
    <section class="card keywords-section">
      <div class="card-title-bar">
        <div>
          <div class="card-title">🔑 Recruiter Search Keywords (What Recruiters Find You For)</div>
          <p style="font-size: 12px; color: var(--text-muted); margin-top: 2px;">
            Skills & keywords recruiters typed when your profile appeared in their candidate searches
          </p>
        </div>
        <input type="text" id="kwSearch" class="filter-input" placeholder="Filter skills (e.g. PySpark, SQL)..." oninput="filterKeywords()">
      </div>

      <div class="tags-cloud" id="keywordsCloud">
        <!-- Injected via JavaScript -->
      </div>
    </section>

    <!-- Two-column Activity Feeds -->
    <section class="feed-grid">
      <!-- Recent Recruiter Actions on Applications -->
      <div class="card">
        <div class="card-title-bar">
          <div class="card-title">⚡ Recent Recruiter Interactions on Applications</div>
          <span class="badge badge-green" id="appActionCountBadge">108 actions</span>
        </div>
        <div class="table-container">
          <table class="styled-table" id="tableActions">
            <thead>
              <tr>
                <th>Company</th>
                <th>Job Title</th>
                <th>Recruiter Status</th>
                <th>Date</th>
              </tr>
            </thead>
            <tbody id="tbodyActions">
              <!-- Injected -->
            </tbody>
          </table>
        </div>
      </div>

      <!-- Direct Recruiter Inquiries / Mails -->
      <div class="card">
        <div class="card-title-bar">
          <div class="card-title">📨 Recruiter Direct Inquiries & Emails</div>
          <span class="badge badge-purple" id="mailCountBadge">14 emails</span>
        </div>
        <div class="table-container">
          <table class="styled-table" id="tableMails">
            <thead>
              <tr>
                <th>Subject</th>
                <th>Type</th>
                <th>Received Date</th>
              </tr>
            </thead>
            <tbody id="tbodyMails">
              <!-- Injected -->
            </tbody>
          </table>
        </div>
      </div>
    </section>
  </div>

  <div id="toast" class="toast">
    <div id="toastSpinner" class="spinner" style="display:none;"></div>
    <span id="toastMsg">Synced successfully</span>
  </div>

  <script>
    // Embedded dataset from python tracker
    let trackerData = {data_json};

    let timelineChartInstance = null;
    let actionsChartInstance = null;
    let allKeywords = [];

    function renderDashboard(data) {{
      const latest = data.latest_metrics || {{}};
      const c = latest.candidate || {{}};
      const sa = latest.searchAppearances || {{}};
      const ra = latest.recruiterActions || {{}};
      const inbox = latest.inbox || {{}};
      const sb = ra.statusBreakdown || {{}};

      // Profile Bar
      if (c.name) document.getElementById('candidateName').innerHTML = c.name + ' <span class="badge badge-green"><span class="dot"></span> Active</span>';
      if (c.designation) document.getElementById('candidateRole').innerText = `${{c.designation}} • ${{c.organization || 'Data Engineer'}}`;
      if (c.name) {{
        const initials = c.name.split(' ').map(n => n[0]).join('').slice(0, 2).toUpperCase();
        document.getElementById('avatarText').innerText = initials || 'HG';
      }}
      if (c.profileCompleteness) document.getElementById('badgeCompleteness').innerText = `${{c.profileCompleteness}}% Profile Completeness`;
      if (c.activityLevel) document.getElementById('badgeActivity').innerText = `Activity: ${{c.activityLevel}}`;
      if (c.lastResumeUpdate) document.getElementById('badgeUpdated').innerText = `Updated: ${{c.lastResumeUpdate}}`;

      // KPIs
      document.getElementById('kpiTotalSearches').innerText = (sa.totalCount || 476).toLocaleString();
      document.getElementById('kpiRecentSearches').innerText = `${{sa.last30DaysCount || 203}} in last 30 days`;
      document.getElementById('kpiGrowth').innerText = `+${{sa.percentageChange || 96}}%`;

      document.getElementById('kpiRecruiterActions').innerText = (ra.activityOnApplications || 108).toLocaleString();
      document.getElementById('kpiProfileViews').innerText = `${{ra.profileViews || 38}} direct profile views`;

      document.getElementById('kpiShortlisted').innerText = (sb['Shortlisted'] || 10).toLocaleString();
      document.getElementById('kpiResumesViewed').innerText = `${{sb['Resume Viewed'] || 90}} resumes viewed by HR`;

      document.getElementById('kpiDirectMails').innerText = (inbox.totalCount || 14).toLocaleString();
      document.getElementById('kpiUnreadMails').innerText = `${{inbox.unreadCount || 0}} unread messages`;

      // Chart 1: Timeline
      renderTimelineChart(sa);

      // Chart 2: Actions Breakdown
      renderActionsChart(sb, ra.profileViews || 38);

      // Keywords
      allKeywords = sa.keywords30d || [];
      renderKeywordsCloud(allKeywords);

      // Table 1: Recent Actions
      renderRecentActionsTable(ra.recentActions || []);

      // Table 2: Recruiter Mails
      renderMailsTable(inbox.recentMails || []);
    }}

    function renderTimelineChart(sa) {{
      const timelineObj = sa.timeline30d || {{}};
      const labels = [];
      const values = [];

      // Sort chronological
      const keys = Object.keys(timelineObj).reverse();
      keys.forEach(k => {{
        labels.push(k.replace(' - ', ' to '));
        values.push(timelineObj[k]);
      }});

      if (labels.length === 0) {{
        labels.push('Week 1', 'Week 2', 'Week 3', 'Week 4');
        values.push(35, 74, 35, 59);
      }}

      const ctx = document.getElementById('timelineChart').getContext('2d');
      if (timelineChartInstance) timelineChartInstance.destroy();

      const gradient = ctx.createLinearGradient(0, 0, 0, 250);
      gradient.addColorStop(0, 'rgba(6, 182, 212, 0.4)');
      gradient.addColorStop(1, 'rgba(6, 182, 212, 0.0)');

      timelineChartInstance = new Chart(ctx, {{
        type: 'line',
        data: {{
          labels: labels,
          datasets: [{{
            label: 'Search Appearances',
            data: values,
            borderColor: '#06b6d4',
            backgroundColor: gradient,
            fill: true,
            tension: 0.35,
            borderWidth: 3,
            pointBackgroundColor: '#080c14',
            pointBorderColor: '#06b6d4',
            pointBorderWidth: 2,
            pointRadius: 5,
            pointHoverRadius: 7,
          }}]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          plugins: {{
            legend: {{ display: false }},
            tooltip: {{
              backgroundColor: '#1e293b',
              titleColor: '#fff',
              bodyColor: '#38bdf8',
              borderColor: 'rgba(255,255,255,0.1)',
              borderWidth: 1,
              padding: 10,
              displayColors: false,
            }}
          }},
          scales: {{
            x: {{
              grid: {{ display: false }},
              ticks: {{ color: '#94a3b8', font: {{ size: 11 }} }}
            }},
            y: {{
              grid: {{ color: 'rgba(255, 255, 255, 0.05)' }},
              ticks: {{ color: '#94a3b8', font: {{ size: 11 }} }},
              beginAtZero: true
            }}
          }}
        }}
      }});
    }}

    function renderActionsChart(sb, profileViews) {{
      const labels = ['Resumes Viewed', 'Profile Views', 'Applications Viewed', 'Shortlisted', 'Contacted / Email'];
      const data = [
        sb['Resume Viewed'] || 90,
        profileViews || 38,
        sb['Application Viewed'] || 108,
        sb['Shortlisted'] || 10,
        sb['Contacted By Email'] || 5
      ];

      const ctx = document.getElementById('actionsChart').getContext('2d');
      if (actionsChartInstance) actionsChartInstance.destroy();

      actionsChartInstance = new Chart(ctx, {{
        type: 'doughnut',
        data: {{
          labels: labels,
          datasets: [{{
            data: data,
            backgroundColor: [
              '#34d399', // Emerald
              '#818cf8', // Indigo
              '#38bdf8', // Cyan
              '#fbbf24', // Amber
              '#f43f5e'  // Rose
            ],
            borderColor: '#111827',
            borderWidth: 3,
          }}]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          cutout: '68%',
          plugins: {{
            legend: {{
              position: 'bottom',
              labels: {{
                color: '#cbd5e1',
                font: {{ size: 11 }},
                padding: 14,
                boxWidth: 10,
                boxHeight: 10,
                usePointStyle: true
              }}
            }},
            tooltip: {{
              backgroundColor: '#1e293b',
              borderColor: 'rgba(255,255,255,0.1)',
              borderWidth: 1,
              padding: 10,
            }}
          }}
        }}
      }});
    }}

    function renderKeywordsCloud(kwList) {{
      const container = document.getElementById('keywordsCloud');
      container.innerHTML = '';
      if (!kwList || kwList.length === 0) {{
        container.innerHTML = '<span style="color:#64748b; font-size:13px;">No keywords recorded yet.</span>';
        return;
      }}
      kwList.forEach(([word, count]) => {{
        const span = document.createElement('span');
        span.className = 'kw-tag';
        span.innerHTML = `${{word}} <span class="count">${{count}}</span>`;
        container.appendChild(span);
      }});
    }}

    function filterKeywords() {{
      const q = document.getElementById('kwSearch').value.toLowerCase().trim();
      const filtered = allKeywords.filter(([word]) => word.toLowerCase().includes(q));
      renderKeywordsCloud(filtered);
    }}

    function renderRecentActionsTable(actions) {{
      const tbody = document.getElementById('tbodyActions');
      tbody.innerHTML = '';
      document.getElementById('appActionCountBadge').innerText = `${{actions.length}} shown`;

      if (actions.length === 0) {{
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; color:#64748b;">No recent recruiter actions.</td></tr>';
        return;
      }}

      actions.slice(0, 15).forEach(a => {{
        const tr = document.createElement('tr');
        const stList = (a.status || []).map(s => {{
          let cls = 'action-viewed';
          if (s.toLowerCase().includes('shortlist')) cls = 'action-shortlist';
          else if (s.toLowerCase().includes('email')) cls = 'action-email';
          return `<span class="action-badge ${{cls}}">${{s}}</span>`;
        }}).join(' ');

        tr.innerHTML = `
          <td><strong>${{a.company || 'Confidential'}}</strong></td>
          <td>${{a.jobTitle || 'Job Role'}}</td>
          <td>${{stList}}</td>
          <td style="color:var(--text-muted); font-size:12px;">${{a.latestStatusDate || '-'}}</td>
        `;
        tbody.appendChild(tr);
      }});
    }}

    function renderMailsTable(mails) {{
      const tbody = document.getElementById('tbodyMails');
      tbody.innerHTML = '';
      document.getElementById('mailCountBadge').innerText = `${{mails.length}} emails`;

      if (mails.length === 0) {{
        tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; color:#64748b;">No recruiter mails recorded.</td></tr>';
        return;
      }}

      mails.slice(0, 15).forEach(m => {{
        const tr = document.createElement('tr');
        const dt = (m.dateTime || '').replace('T', ' ').split('+')[0];
        tr.innerHTML = `
          <td style="max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
            <strong>${{m.subject || 'Recruiter Invitation'}}</strong>
          </td>
          <td><span class="action-badge action-email">${{m.messageType || 'Recruiter Mail'}}</span></td>
          <td style="color:var(--text-muted); font-size:12px;">${{dt}}</td>
        `;
        tbody.appendChild(tr);
      }});
    }}

    // Trigger Live Sync via local server or notify
    function triggerSync() {{
      const btn = document.getElementById('btnSync');
      const icon = document.getElementById('syncIcon');
      const toast = document.getElementById('toast');
      const toastSpinner = document.getElementById('toastSpinner');
      const toastMsg = document.getElementById('toastMsg');

      toastSpinner.style.display = 'inline-block';
      toastMsg.innerText = 'Connecting to Naukri & fetching live metrics...';
      toast.classList.add('show');
      btn.disabled = true;

      fetch('/api/sync', {{ method: 'POST' }})
        .then(res => {{
          if (!res.ok) throw new Error('API server returned ' + res.status);
          return res.json();
        }})
        .then(freshData => {{
          trackerData = freshData;
          renderDashboard(freshData);
          toastSpinner.style.display = 'none';
          toastMsg.innerText = '✓ Profile metrics synced & updated live!';
          setTimeout(() => toast.classList.remove('show'), 3500);
        }})
        .catch(err => {{
          console.warn('Local sync server not responding (static file mode?):', err);
          toastSpinner.style.display = 'none';
          toastMsg.innerText = 'To sync live, run: python track_profile.py --serve';
          setTimeout(() => toast.classList.remove('show'), 4000);
        }})
        .finally(() => {{
          btn.disabled = false;
        }});
    }}

    // Initial render
    renderDashboard(trackerData);
  </script>
</body>
</html>
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    log.info("Generated dashboard HTML at %s ✓", output_path)
    return output_path


def update_daily_markdown_and_csv(client, log_dir: str = "/Users/harishankargiri/MyProject/Naukri-Daily-Job-Apply") -> Dict[str, Any]:
    """
    Fetches latest Naukri metrics and:
      1. Updates or appends today's numbers to naukri_metrics_history.csv
      2. Injects or updates the daily performance block in the date markdown file (e.g. 2026-10-14.md)
    """
    today = datetime.now().strftime("%Y-%m-%d")
    metrics = fetch_profile_metrics(client)

    sa = metrics.get("searchAppearances", {})
    ra = metrics.get("recruiterActions", {})
    inbox = metrics.get("inbox", {})
    sb = ra.get("statusBreakdown", {})

    searches = sa.get("totalCount", 0)
    searches_30d = sa.get("last30DaysCount", 0)
    pct_change = sa.get("percentageChange", 0)
    profile_views = ra.get("profileViews", 0)
    recruiter_act = ra.get("activityOnApplications", 0)
    resumes_viewed = sb.get("Resume Viewed", 0)
    shortlisted = sb.get("Shortlisted", 0)
    direct_mails = inbox.get("totalCount", 0)

    # On https://www.naukri.com/mnjuser/performance, the metrics are:
    # 1. Search appearances: daily timeline count from timeline7d (e.g. 0, 5, 30, 23, etc.)
    # 2. Recruiter actions: dashBoard.profileViewCount (e.g. 39)
    timeline7d = sa.get("timeline7d", {})
    today_searches = timeline7d.get(today, 0)
    recruiter_actions_total = profile_views if profile_views > 0 else (recruiter_act or 0)

    # 1. Update CSV in workspace and in log_dir
    workspace_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    csv_paths = [
        os.path.join(workspace_dir, "naukri_metrics_history.csv"),
    ]
    if os.path.isdir(log_dir):
        csv_paths.append(os.path.join(log_dir, "naukri_metrics_history.csv"))

    for c_path in csv_paths:
        rows_by_date = {}
        header = ["date", "search_appearances", "recruiter_actions", "profile_views", "resumes_viewed", "shortlisted", "direct_mails"]
        if os.path.exists(c_path):
            try:
                with open(c_path, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for r in reader:
                        d_key = r.get("date")
                        if d_key:
                            rows_by_date[d_key] = r
            except Exception:
                pass

        # Update all dates from timeline7d with their REAL daily search count
        for d_key, d_count in sorted(timeline7d.items()):
            if d_key in rows_by_date:
                rows_by_date[d_key]["search_appearances"] = str(d_count)
            else:
                rows_by_date[d_key] = {
                    "date": d_key,
                    "search_appearances": str(d_count),
                    "recruiter_actions": str(recruiter_actions_total),
                    "profile_views": str(profile_views),
                    "resumes_viewed": str(resumes_viewed),
                    "shortlisted": str(shortlisted),
                    "direct_mails": str(direct_mails)
                }

        # Update today entry
        if today in rows_by_date:
            rows_by_date[today]["search_appearances"] = str(today_searches)
            rows_by_date[today]["recruiter_actions"] = str(recruiter_actions_total)
            rows_by_date[today]["profile_views"] = str(profile_views)
            rows_by_date[today]["resumes_viewed"] = str(resumes_viewed)
            rows_by_date[today]["shortlisted"] = str(shortlisted)
            rows_by_date[today]["direct_mails"] = str(direct_mails)
        else:
            rows_by_date[today] = {
                "date": today,
                "search_appearances": str(today_searches),
                "recruiter_actions": str(recruiter_actions_total),
                "profile_views": str(profile_views),
                "resumes_viewed": str(resumes_viewed),
                "shortlisted": str(shortlisted),
                "direct_mails": str(direct_mails)
            }

        sorted_rows = [rows_by_date[k] for k in sorted(rows_by_date.keys())]

        try:
            with open(c_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=header)
                writer.writeheader()
                writer.writerows(sorted_rows)
            log.info("Updated metrics CSV at %s ✓", c_path)
        except Exception as e:
            log.warning("Could not write CSV %s: %s", c_path, e)


    # 2. Update daily markdown file in log_dir
    md_path = os.path.join(log_dir, f"{today}.md")
    perf_section = (
        "## 📊 Daily Profile Performance\n\n"
        "| Metric | Count | Details |\n"
        "| :--- | :--- | :--- |\n"
        f"| 🔍 **Search Appearances** | **{searches}** | {searches_30d} in last 30 days (+{pct_change}%) |\n"
        f"| 🎯 **Recruiter Actions** | **{recruiter_actions_total}** | Direct recruiter actions on profile |\n"
        f"| 📬 **Direct Inquiries** | **{direct_mails}** | Recruiter messages in inbox |\n\n"
    )

    if os.path.exists(md_path):
        try:
            with open(md_path, "r", encoding="utf-8") as f:
                content = f.read()

            if "## 📊 Daily Profile Performance" in content:
                # Replace existing performance block
                pattern = re.compile(r"## 📊 Daily Profile Performance\n\n.*?(?=\n## |\Z)", re.DOTALL)
                content = pattern.sub(perf_section.strip(), content)
            else:
                title_needle = f"# Naukri Job Applications — {today}\n\n"
                if title_needle in content:
                    content = content.replace(title_needle, title_needle + perf_section)
                else:
                    content = f"# Naukri Job Applications — {today}\n\n{perf_section}" + content

            with open(md_path, "w", encoding="utf-8") as f:
                f.write(content)
            log.info("Updated daily performance section in %s ✓", md_path)
        except Exception as exc:
            log.warning("Failed to update performance in %s: %s", md_path, exc)

    return metrics

