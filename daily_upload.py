"""
daily_upload.py — Naukri resume auto-uploader + smart job applier

Modes:
  --now         Full run: resume upload + headline update + apply jobs
  --apply-only  Apply jobs only (no resume upload). Used for mid-day runs.

Schedule (via launchd on macOS):
  09:15  --now          (full run)
  11:15  --apply-only
  13:15  --apply-only
  14:00  --now          (full run)
  16:15  --apply-only

Job Apply Strategy:
  Tier 1  (first priority): Data Engineer, Associate Data Engineer + multi-keyword
  Tier 2  (fallback):       Data Analyst, Software Engineer (Backend)
  Tier 3  (last resort):    Full Stack Developer
  → Only descends to the next tier if the current tier yields 0 applicable jobs.

Daily Cap:
  MAX_APPLIES_PER_DAY is tracked in apply_count_today.json.
  Each run contributes at most MAX_APPLIES_PER_RUN applications.
  Once the daily cap is hit, apply steps are skipped silently.
"""

import os
import sys
import time
import json
import random
import logging
import schedule
import requests
from datetime import datetime
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))
from src.client.naukri_client import NaukriLoginClient
from src.client.job_client import NaukriJobClient
from src.exceptions.exceptions import NaukriClientError

# ── logging ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("daily_upload.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

load_dotenv()

# ── credentials ──────────────────────────────────────────────────
NAUKRI_USERNAME  = os.getenv("NAUKRI_USERNAME", "")
NAUKRI_PASSWORD  = os.getenv("NAUKRI_PASSWORD", "")
GDRIVE_FILE_ID   = os.getenv("GDRIVE_FILE_ID", "")
RESUME_BASE_NAME = os.getenv("RESUME_BASE_NAME", "resume")
HEADLINE         = os.getenv("NAUKRI_HEADLINE", "")

# ── schedule ─────────────────────────────────────────────────────
UPLOAD_TIMES = ["09:15", "14:00"]   # full runs (resume + apply)

# ── daily apply cap ──────────────────────────────────────────────
MAX_APPLIES_PER_DAY = int(os.getenv("MAX_APPLIES_PER_DAY", "20"))
MAX_APPLIES_PER_RUN = int(os.getenv("MAX_APPLIES_PER_RUN", "5"))
DAILY_COUNT_FILE    = "apply_count_today.json"

# ── tiered keywords ──────────────────────────────────────────────
#   Each tier is searched only if the previous tier produced 0 results
#   to apply to (not 0 results found, but 0 non-external, non-duplicate jobs).
TIER1_KEYWORDS = [kw.strip() for kw in os.getenv(
    "JOB_KEYWORDS_TIER1",
    "Data Engineer,Associate Data Engineer,Data Engineer PySpark SQL Azure Databricks"
).split(",") if kw.strip()]

TIER2_KEYWORDS = [kw.strip() for kw in os.getenv(
    "JOB_KEYWORDS_TIER2",
    "Data Analyst,Software Engineer Backend"
).split(",") if kw.strip()]

TIER3_KEYWORDS = [kw.strip() for kw in os.getenv(
    "JOB_KEYWORDS_TIER3",
    "Full Stack Developer"
).split(",") if kw.strip()]

# ── job search config ─────────────────────────────────────────────
JOB_LOCATION    = os.getenv("JOB_LOCATION", "")
JOB_EXPERIENCE  = int(os.getenv("JOB_EXPERIENCE", "1"))
PROFILE_SKILLS  = [s.strip().lower() for s in os.getenv("PROFILE_SKILLS", "").split(",") if s.strip()]
APPLY_JOBS      = os.getenv("APPLY_JOBS", "False").lower() in ("true", "1", "yes")


# ═══════════════════════════════════════════════════════════════════
# Daily cap helpers
# ═══════════════════════════════════════════════════════════════════

def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def get_today_applied_count() -> int:
    """Returns how many jobs have already been applied to today."""
    if not os.path.exists(DAILY_COUNT_FILE):
        return 0
    try:
        with open(DAILY_COUNT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("date") == _today_str():
            return int(data.get("count", 0))
    except Exception:
        pass
    return 0


def increment_today_count(n: int = 1) -> int:
    """Adds n to today's applied count. Returns new total."""
    current = get_today_applied_count()
    new_total = current + n
    with open(DAILY_COUNT_FILE, "w", encoding="utf-8") as f:
        json.dump({"date": _today_str(), "count": new_total}, f)
    return new_total


def daily_remaining() -> int:
    """How many more applications are allowed today."""
    return max(0, MAX_APPLIES_PER_DAY - get_today_applied_count())


# ═══════════════════════════════════════════════════════════════════
# Internet check
# ═══════════════════════════════════════════════════════════════════

def wait_for_internet(timeout: int = 300, delay: int = 10) -> bool:
    """Waits for active internet connection (useful after waking from sleep)."""
    log.info("Checking internet connection...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get("https://www.google.com", timeout=5)
            if r.status_code == 200:
                log.info("Internet connection is active.")
                return True
        except requests.RequestException:
            pass
        log.warning("No internet yet. Retrying in %ds...", delay)
        time.sleep(delay)
    return False


# ═══════════════════════════════════════════════════════════════════
# Resume helpers
# ═══════════════════════════════════════════════════════════════════

def dated_filename() -> str:
    """e.g. Harishankar_Giri_Data_Engineer_Resume_22May2026.pdf"""
    return f"{RESUME_BASE_NAME}_{datetime.now().strftime('%d%b%Y')}.pdf"


def download_from_gdrive(file_id: str) -> bytes:
    """Downloads a file from Google Drive (handles virus-scan confirm redirect)."""
    session = requests.Session()

    url  = f"https://drive.google.com/uc?export=download&id={file_id}"
    resp = session.get(url, timeout=30)
    resp.raise_for_status()

    if b"virus scan warning" in resp.content.lower() or b"confirm" in resp.content[:500].lower():
        import re
        token_match = re.search(rb'confirm=([0-9A-Za-z_\-]+)', resp.content)
        if token_match:
            token = token_match.group(1).decode()
            resp  = session.get(f"{url}&confirm={token}", timeout=30)
            resp.raise_for_status()

    if resp.content[:4] != b"%PDF":
        raise ValueError(
            "Downloaded content is not a valid PDF. "
            "Check GDRIVE_FILE_ID and ensure the file is publicly shared."
        )

    log.info("Downloaded %.1f KB from GDrive", len(resp.content) / 1024)
    return resp.content


# ═══════════════════════════════════════════════════════════════════
# Job apply helpers
# ═══════════════════════════════════════════════════════════════════

def log_applied_job(job) -> None:
    """Appends a row to the daily markdown apply log."""
    directory = "/Users/harishankargiri/MyProject/Naukri-Daily-Job-Apply"
    try:
        os.makedirs(directory, exist_ok=True)
        today    = datetime.now().strftime("%Y-%m-%d")
        filepath = os.path.join(directory, f"{today}.md")
        time_str = datetime.now().strftime("%H:%M:%S")
        job_url  = (
            job.apply_link
            if hasattr(job, "apply_link") and job.apply_link
            else f"https://www.naukri.com/job-listings-{job.job_id}"
        )
        file_exists = os.path.exists(filepath)
        with open(filepath, "a", encoding="utf-8") as f:
            if not file_exists:
                f.write(f"# Naukri Job Applications — {today}\n\n")
                f.write("| Time | Company | Job Title | Job ID | Link |\n")
                f.write("| --- | --- | --- | --- | --- |\n")
            company = job.company.replace("|", "\\|").replace("\n", " ").strip()
            title   = job.title.replace("|", "\\|").replace("\n", " ").strip()
            f.write(f"| {time_str} | {company} | {title} | `{job.job_id}` | [View Job]({job_url}) |\n")
        log.info("Logged application → %s", filepath)
    except Exception as exc:
        log.error("Failed to write apply log: %s", exc)


def score_job(job) -> int:
    """Ranks a job by how many profile skills appear in title, tags, or description."""
    if not PROFILE_SKILLS:
        return 0
    score = 0
    title_lower = job.title.lower()
    desc_lower  = (job.description or "").lower()
    for skill in PROFILE_SKILLS:
        if skill in title_lower:
            score += 10
        for tag in job.tags:
            if skill == tag.lower() or skill in tag.lower():
                score += 5
        if desc_lower and skill in desc_lower:
            score += 1
    return score


def search_tier(job_client: NaukriJobClient, keywords: list[str], seen_ids: set) -> list:
    """
    Searches all keywords in a tier across 2 pages. Returns unique jobs
    not already in seen_ids, ranked by skill relevance score (descending).
    """
    tier_jobs = []
    for keyword in keywords:
        for page_num in [1, 2]:
            try:
                results  = job_client.search_jobs(
                    keyword=keyword,
                    location=JOB_LOCATION,
                    experience=JOB_EXPERIENCE,
                    page=page_num,
                    job_age=2,        # only jobs posted in last 2 days
                )
                new_jobs = [j for j in results if j.job_id not in seen_ids]
                seen_ids.update(j.job_id for j in new_jobs)
                tier_jobs.extend(new_jobs)
                log.info("    [%s | p%d]  fetched=%d  new=%d",
                         keyword, page_num, len(results), len(new_jobs))
                if not results:
                    break
            except Exception as exc:
                log.error("    Search error [%s | p%d]: %s", keyword, page_num, exc)
    # Sort by relevance score descending
    return sorted(tier_jobs, key=score_job, reverse=True)


def apply_single_job(job_client: NaukriJobClient, job, run_source: str = "search") -> bool:
    """
    Attempts to apply to a single job. Returns True on success, False otherwise.
    Handles questionnaires automatically.
    """
    mandatory = job.tags[:2] if job.tags else []
    optional  = job.tags[2:] if len(job.tags) > 2 else []

    sleep_secs = random.uniform(4.0, 8.0)
    log.info("  → Pausing %.1fs before applying...", sleep_secs)
    time.sleep(sleep_secs)

    try:
        result  = job_client.apply_job(job, mandatory_skills=mandatory,
                                       optional_skills=optional, source=run_source)
        job_res = (result.get("jobs") or [{}])[0]

        if job_res.get("questionnaire"):
            log.info("  → Questionnaire detected — auto-solving...")
            q_result  = job_client.handle_static_questionnaire_and_apply(
                job=job,
                questionnaire=job_res["questionnaire"],
                sid=result.get("sid", ""),
                mandatory_skills=mandatory,
                optional_skills=optional,
                source=run_source,
            )
            q_job_res = (q_result.get("jobs") or [{}])[0]
            if q_result.get("error") or q_job_res.get("questionnaire"):
                log.warning("  → Skipped: Complex questionnaire could not be solved.")
                return False
            log.info("  → ✅ Applied (with auto-solved questionnaire)!")
            return True

        log.info("  → ✅ Applied successfully!")
        return True

    except Exception as exc:
        log.error("  → ❌ Apply failed: %s", exc)
        return False


def apply_to_jobs(login_client: NaukriLoginClient) -> int:
    """
    Core apply loop. Searches tiers in order and applies until:
      - MAX_APPLIES_PER_RUN is reached, OR
      - Daily cap (MAX_APPLIES_PER_DAY) is reached

    Returns the number of jobs applied in this run.
    """
    remaining_today = daily_remaining()
    if remaining_today <= 0:
        log.info("Daily apply cap of %d already reached. Skipping apply.", MAX_APPLIES_PER_DAY)
        return 0

    run_limit = min(MAX_APPLIES_PER_RUN, remaining_today)
    log.info(
        "Apply budget — per run: %d | remaining today: %d | effective this run: %d",
        MAX_APPLIES_PER_RUN, remaining_today, run_limit,
    )

    try:
        job_client = NaukriJobClient(login_client)
    except Exception as exc:
        log.error("Failed to init NaukriJobClient: %s", exc)
        return 0

    seen_ids     = set()
    applied_run  = 0

    tiers = [
        ("Tier 1 — Data Engineer / Associate", TIER1_KEYWORDS),
        ("Tier 2 — Data Analyst / SW Engineer", TIER2_KEYWORDS),
        ("Tier 3 — Full Stack (last resort)",   TIER3_KEYWORDS),
    ]

    for tier_name, keywords in tiers:
        if applied_run >= run_limit:
            break

        log.info("─" * 55)
        log.info("Searching %s...", tier_name)
        jobs = search_tier(job_client, keywords, seen_ids)
        log.info("Found %d unique candidate jobs in %s", len(jobs), tier_name)

        if not jobs:
            log.info("No jobs found in %s — falling back to next tier.", tier_name)
            continue

        tier_applied = 0

        for job in jobs:
            if applied_run >= run_limit:
                break

            log.info("Evaluating: '%s' @ '%s'  (id=%s)", job.title, job.company, job.job_id)

            # Skip external apply jobs
            try:
                if job_client.is_external_apply(job.job_id):
                    log.info("  → Skipping: External company apply (not supported).")
                    continue
            except Exception as exc:
                log.warning("  → Could not verify apply type (%s). Skipping.", exc)
                continue

            success = apply_single_job(job_client, job)
            if success:
                log_applied_job(job)
                increment_today_count(1)
                applied_run  += 1
                tier_applied += 1

        if tier_applied > 0:
            log.info("%s produced %d successful applications — not descending further.",
                     tier_name, tier_applied)
            break   # ← Only fall to next tier if THIS tier got 0 applies
        else:
            log.info("No successful applications in %s — trying next tier.", tier_name)

    log.info("─" * 55)
    log.info("Run complete. Applied: %d this run | %d total today (cap: %d).",
             applied_run, get_today_applied_count(), MAX_APPLIES_PER_DAY)
    return applied_run


# ═══════════════════════════════════════════════════════════════════
# Full upload cycle (resume + headline + apply)
# ═══════════════════════════════════════════════════════════════════

def upload_once() -> bool:
    """
    Full cycle: internet check → validate config → download resume →
    login → upload → headline → (optional) apply.
    """
    log.info("=" * 55)
    log.info("FULL RUN — %s", datetime.now().strftime("%Y-%m-%d %H:%M"))

    if not wait_for_internet():
        log.error("No internet. Aborting.")
        log.info("=" * 55)
        return False

    missing = [k for k, v in {
        "NAUKRI_USERNAME": NAUKRI_USERNAME,
        "NAUKRI_PASSWORD": NAUKRI_PASSWORD,
        "GDRIVE_FILE_ID":  GDRIVE_FILE_ID,
    }.items() if not v]
    if missing:
        log.error("Missing .env vars: %s", ", ".join(missing))
        return False

    # ── download resume ──────────────────────────────────────────
    try:
        pdf_bytes = download_from_gdrive(GDRIVE_FILE_ID)
    except Exception as exc:
        log.error("GDrive download failed: %s", exc)
        return False

    filename = dated_filename()
    log.info("Filename: %s", filename)
    tmp_path = f"/tmp/{filename}"
    with open(tmp_path, "wb") as f:
        f.write(pdf_bytes)

    # ── login ────────────────────────────────────────────────────
    client = NaukriLoginClient(NAUKRI_USERNAME, NAUKRI_PASSWORD)
    try:
        client.login()
        log.info("Login successful")
    except NaukriClientError as exc:
        log.error("Login failed: %s", exc)
        return False

    # ── upload resume ────────────────────────────────────────────
    try:
        result = client.update_resume(tmp_path)
        log.info("Resume uploaded ✓  (profile_id=%s  status=%s)",
                 result.profile_id, result.status_code)
    except NaukriClientError as exc:
        log.error("Resume upload failed: %s", exc)
        return False
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    # ── headline update ──────────────────────────────────────────
    if HEADLINE:
        base = HEADLINE.strip().rstrip("|").strip()
        # Morning → append " |" so Naukri shows the profile as "just updated"
        formatted = f"{base} |" if datetime.now().hour < 12 else base
        try:
            client.update_profile(headline=formatted)
            log.info("Headline updated → '%s'  ✓", formatted)
        except NaukriClientError as exc:
            log.warning("Headline update failed (non-fatal): %s", exc)

    # ── apply jobs ───────────────────────────────────────────────
    if APPLY_JOBS:
        apply_to_jobs(client)

    log.info("Full cycle complete ✓")
    log.info("=" * 55)
    return True


# ═══════════════════════════════════════════════════════════════════
# Apply-only cycle (no resume upload — used for mid-day runs)
# ═══════════════════════════════════════════════════════════════════

def apply_only_run() -> bool:
    """
    Lightweight run: login + apply only. No resume download or upload.
    Used for the 11:15, 13:15, 16:15 scheduled triggers.
    """
    log.info("=" * 55)
    log.info("APPLY-ONLY RUN — %s", datetime.now().strftime("%Y-%m-%d %H:%M"))

    if not wait_for_internet():
        log.error("No internet. Aborting.")
        log.info("=" * 55)
        return False

    if not APPLY_JOBS:
        log.info("APPLY_JOBS is False in .env — nothing to do.")
        return True

    remaining = daily_remaining()
    if remaining <= 0:
        log.info("Daily cap of %d already reached. Skipping.", MAX_APPLIES_PER_DAY)
        log.info("=" * 55)
        return True

    client = NaukriLoginClient(NAUKRI_USERNAME, NAUKRI_PASSWORD)
    try:
        client.login()
        log.info("Login successful")
    except NaukriClientError as exc:
        log.error("Login failed: %s", exc)
        return False

    apply_to_jobs(client)

    log.info("Apply-only cycle complete ✓")
    log.info("=" * 55)
    return True


# ═══════════════════════════════════════════════════════════════════
# Scheduler (fallback for non-launchd usage)
# ═══════════════════════════════════════════════════════════════════

def run_scheduler() -> None:
    for t in UPLOAD_TIMES:
        schedule.every().day.at(t).do(upload_once)
        log.info("Scheduled full run at %s", t)
    log.info("Scheduler running. Press Ctrl+C to stop.")
    while True:
        schedule.run_pending()
        time.sleep(30)


# ═══════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Naukri daily uploader + smart job applier")
    group  = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--now",
        action="store_true",
        help="Full run: resume upload + headline + apply (used at 9:15 AM and 2:00 PM)",
    )
    group.add_argument(
        "--apply-only",
        action="store_true",
        dest="apply_only",
        help="Apply-only run: skip resume upload (used at 11:15, 13:15, 16:15)",
    )
    args = parser.parse_args()

    if args.now:
        success = upload_once()
        sys.exit(0 if success else 1)
    elif args.apply_only:
        success = apply_only_run()
        sys.exit(0 if success else 1)
    else:
        run_scheduler()
