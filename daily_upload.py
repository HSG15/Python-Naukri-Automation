"""
daily_upload.py — Naukri resume auto-uploader + smart job applier

Modes:
  --now             Full run: resume upload + headline rotation + apply jobs
  --apply-only      Apply jobs only (no resume upload). Used for mid-day runs.
  --weekly-summary  Generate a weekly summary report of all applications.

Schedule (via launchd on macOS):
  09:15  --now          (full run)
  11:15  --apply-only
  13:15  --apply-only
  14:00  --now          (full run)
  16:15  --apply-only
  Sunday 18:00  --weekly-summary

Job Apply Strategy:
  Keywords: Data Engineer, Associate Data Engineer, PySpark Developer,
            Azure/AWS Data Engineer, ETL Developer, Databricks Developer, Spark Developer
  → Only applies to jobs passing the strict is_job_relevant() relevance filter.
  → Headlines rotate through HEADLINE_VARIANTS on each full run for profile freshness.
  → Applied jobs are tracked in applied_jobs_history.json to prevent cross-day duplicates.
  → Search window expands from 2 days to 3 days on weekends (fewer fresh postings).

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
from datetime import datetime, timedelta
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

# ── schedule ─────────────────────────────────────────────────────
UPLOAD_TIMES = ["09:15", "14:00"]   # full runs (resume + apply)

# ── daily apply cap ──────────────────────────────────────────────
MAX_APPLIES_PER_DAY = int(os.getenv("MAX_APPLIES_PER_DAY", "20"))
MAX_APPLIES_PER_RUN = int(os.getenv("MAX_APPLIES_PER_RUN", "5"))
DAILY_COUNT_FILE    = "apply_count_today.json"
APPLIED_HISTORY_FILE = "applied_jobs_history.json"  # cross-day dedup
HEADLINE_STATE_FILE  = "headline_state.json"         # rotation state
RUN_HISTORY_FILE     = "run_history.json"            # tracked scheduled runs
SCHEDULED_SLOTS = [
    {"time": "09:15", "type": "full"},
    {"time": "11:15", "type": "apply"},
    {"time": "13:15", "type": "apply"},
    {"time": "14:00", "type": "full"},
    {"time": "16:15", "type": "apply"},
]


# Rotating headline variants — cycles on every full run so Naukri's algorithm
# sees a "fresh" profile each time. Each variant leads with a different core skill.
HEADLINE_VARIANTS = [
    "Data Engineer | ~2 YOE | Immediate Joiner | Python | PySpark | Apache Spark | Azure Databricks | ADF | Azure Synapse | ETL/ELT | Delta Lake | AWS | SQL",
    "PySpark Developer | ~2 YOE | Data Engineer | Immediate Joiner | Azure Databricks | ADF | Synapse | Delta Lake | ETL/ELT | Python | SQL | AWS",
    "Azure Data Engineer | ~2 YOE | Immediate Joiner | PySpark | Spark | Databricks | ADF | Synapse | Delta Lake | SQL | Python | AWS | ETL/ELT",
    "ETL Developer | ~2 YOE | Data Engineer | Immediate Joiner | PySpark | Apache Spark | Databricks | ADF | Synapse | Delta Lake | SQL | Python | AWS",
]

# ── search keywords ──────────────────────────────────────────────
JOB_KEYWORDS_STR = os.getenv("JOB_KEYWORDS", "")
if not JOB_KEYWORDS_STR:
    JOB_KEYWORDS_STR = os.getenv(
        "JOB_KEYWORDS_TIER1",
        "Data Engineer,Associate Data Engineer,PySpark Developer,Azure Data Engineer,AWS Data Engineer"
    )
JOB_KEYWORDS = [kw.strip() for kw in JOB_KEYWORDS_STR.split(",") if kw.strip()]


# ── job search config ─────────────────────────────────────────────
JOB_LOCATION    = os.getenv("JOB_LOCATION", "")
JOB_EXPERIENCE  = int(os.getenv("JOB_EXPERIENCE", "1"))
PROFILE_SKILLS  = [s.strip().lower() for s in os.getenv("PROFILE_SKILLS", "").split(",") if s.strip()]
APPLY_JOBS      = os.getenv("APPLY_JOBS", "False").lower() in ("true", "1", "yes")

# ── daily log directory ───────────────────────────────────────────
DAILY_LOG_DIR = "/Users/harishankargiri/MyProject/Naukri-Daily-Job-Apply"


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
# Applied history (cross-day dedup)
# ═══════════════════════════════════════════════════════════════════

def load_applied_history() -> dict:
    """Returns {job_id: {title, company, applied_date}} for all tracked applied jobs."""
    if not os.path.exists(APPLIED_HISTORY_FILE):
        return {}
    try:
        with open(APPLIED_HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def is_already_applied(job_id: str) -> bool:
    """Returns True if we have previously applied to this job_id."""
    return job_id in load_applied_history()


def save_applied_history_entry(job) -> None:
    """Adds job to the persistent applied-history file. Auto-prunes entries older than 30 days."""
    history = load_applied_history()

    # Prune entries older than 30 days to keep the file size manageable
    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    history = {
        jid: meta for jid, meta in history.items()
        if meta.get("applied_date", "1970-01-01") >= cutoff
    }

    history[job.job_id] = {
        "title":        job.title,
        "company":      job.company,
        "applied_date": _today_str(),
    }
    try:
        with open(APPLIED_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
    except Exception as exc:
        log.error("Failed to save applied history: %s", exc)


# ═══════════════════════════════════════════════════════════════════
# Headline rotation
# ═══════════════════════════════════════════════════════════════════

def get_next_headline() -> str:
    """
    Cycles through HEADLINE_VARIANTS on each full run to signal profile activity.
    Morning runs append ' |' to trigger Naukri's 'just updated' indicator.
    Persists current index in HEADLINE_STATE_FILE so it survives restarts.
    """
    state = {}
    if os.path.exists(HEADLINE_STATE_FILE):
        try:
            with open(HEADLINE_STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            pass

    last_index = state.get("last_index", -1)
    next_index = (last_index + 1) % len(HEADLINE_VARIANTS)
    base = HEADLINE_VARIANTS[next_index]

    # Morning run → append " |" so Naukri shows the profile as "just updated"
    formatted = f"{base} |" if datetime.now().hour < 12 else base

    try:
        with open(HEADLINE_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"last_index": next_index, "last_used_date": _today_str()}, f)
    except Exception as exc:
        log.warning("Could not save headline state: %s", exc)

    log.info("Headline variant #%d/%d selected.", next_index + 1, len(HEADLINE_VARIANTS))
    return formatted


# ═══════════════════════════════════════════════════════════════════
# Internet check
# ═══════════════════════════════════════════════════════════════════

def wait_for_internet(timeout: int = 900, delay: int = 10) -> bool:
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

RECOMMENDED_SEEN_FILE = "recommended_seen_jobs.json"


def load_recommended_seen_ids() -> set:
    if not os.path.exists(RECOMMENDED_SEEN_FILE):
        return set()
    try:
        with open(RECOMMENDED_SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_recommended_seen_id(job_id: str) -> None:
    seen = load_recommended_seen_ids()
    seen.add(job_id)
    try:
        with open(RECOMMENDED_SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(list(seen), f)
    except Exception as exc:
        log.error("Failed to save recommended seen ID: %s", exc)


def is_job_relevant(job) -> tuple[bool, str]:
    title_lower = job.title.lower()

    # 1. Blacklist check
    blacklist_words = [
        "teacher", "trainer", "tutor", "instructor", "teaching", "faculty", "professor",
        "content", "creator", "writer", "marketing", "sales", "growth", "seo", "media",
        "embedded", "firmware", "hardware", "iot", "drone", "vlsi", "semiconductor",
        "react", "angular", "vue", "frontend", "front end", "front-end", "android", "ios", "mobile", "flutter", "kotlin", "swift",
        "springboot", "spring boot", "laravel", "wordpress", "django", "flask",
        "ui", "ux", "designer", "graphic", "3d", "animation", "video",
        "intern", "internship",
        "support", "helpdesk", "help desk", "customer", "operations", "admin", "administrator", "l1", "l2",
        "recruiter", "hr", "talent acquisition", "fullstack", "full stack"
    ]

    for word in blacklist_words:
        if word in title_lower:
            return False, f"Blacklisted word '{word}' found in title."

    # Special check for Java or Dotnet / .NET (only allow if Data/Spark is also present)
    if "java" in title_lower or "dotnet" in title_lower or ".net" in title_lower:
        if not any(x in title_lower for x in ["data", "spark", "etl", "warehouse"]):
            return False, "Title contains Java/Dotnet but does not contain data engineering keywords."

    # 2. Core Data Engineering keywords check
    core_de_keywords = [
        "data engineer", "pyspark", "databricks", "etl", "spark", "azure data", "aws data",
        "data warehouse", "dwh", "data pipeline", "big data", "database engineer", "synapse", "adf"
    ]

    if any(kw in title_lower for kw in core_de_keywords):
        return True, "Title contains core Data Engineering keyword."

    # 3. Generic Developer check + Core Skills check
    generic_developer_keywords = [
        "software engineer", "developer", "programmer", "associate", "consultant", "analyst", "engineer", "python developer", "sql developer"
    ]

    if any(kw in title_lower for kw in generic_developer_keywords):
        # Must have at least one core DE skill in tags/skills or description.
        core_de_skills = [
            "pyspark", "spark", "databricks", "adf", "synapse", "etl", "delta lake",
            "azure data factory", "azure data lake", "data warehouse", "dwh", "data pipeline"
        ]

        job_skills = [tag.lower() for tag in getattr(job, "tags", [])]
        desc_lower = (getattr(job, "description", "") or "").lower()

        for skill in core_de_skills:
            if skill in job_skills or skill in desc_lower:
                return True, f"Generic title matched with core skill '{skill}' in tags/description."

        return False, "Generic developer title but lacks core Data Engineering skills in tags/description."

    return False, "Does not match any core Data Engineering title or generic developer patterns."


def log_job_to_markdown(job, source: str = "search") -> None:
    """
    Appends a job entry to the daily markdown log file under the correct section.

    source values:
      "search"      → Section 1: ✅ Applied by Bot (Naukri Direct)
      "recommended" → Section 2: 👍 Share Interest (Recommended Jobs)
      "external"    → Section 3: 🔗 Manual Apply Required (Tier-1 External)
    """
    SECTIONS = ["search", "recommended", "external"]
    SECTION_HEADERS = {
        "search":      "## ✅ Applied by Bot (Naukri Direct)",
        "recommended": "## 👍 Share Interest (Recommended Jobs)",
        "external":    "## 🔗 Manual Apply Required (Tier-1 External)",
    }
    try:
        os.makedirs(DAILY_LOG_DIR, exist_ok=True)
        today    = datetime.now().strftime("%Y-%m-%d")
        filepath = os.path.join(DAILY_LOG_DIR, f"{today}.md")
        time_str = datetime.now().strftime("%H:%M:%S")
        job_url  = (
            job.apply_link
            if hasattr(job, "apply_link") and job.apply_link
            else f"https://www.naukri.com/job-listings-{job.job_id}"
        )
        company = job.company.replace("|", "\\|").replace("\n", " ").strip()
        title   = job.title.replace("|", "\\|").replace("\n", " ").strip()
        new_row = f"| {time_str} | {company} | {title} | `{job.job_id}` | [View Job]({job_url}) |"

        # Buckets keyed by section name
        rows: dict[str, list[str]] = {s: [] for s in SECTIONS}

        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                lines = f.readlines()

            current_section = None
            for line in lines:
                ls = line.strip()
                if not ls:
                    continue
                matched = next(
                    (s for s, h in SECTION_HEADERS.items() if ls.startswith(h)), None
                )
                if matched:
                    current_section = matched
                    continue
                if ls.startswith("| Time |") or ls.startswith("| --- |"):
                    continue
                if ls.startswith("|") and current_section in rows:
                    rows[current_section].append(ls)

        # Append new row to the correct bucket (dedup by job_id)
        if not any(f"`{job.job_id}`" in r for r in rows[source]):
            rows[source].append(new_row)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"# Naukri Job Applications — {today}\n\n")
            for sec in SECTIONS:
                sec_rows = rows[sec]
                f.write(f"{SECTION_HEADERS[sec]}\n\n")
                f.write("| Time | Company | Job Title | Job ID | Link |\n")
                f.write("| --- | --- | --- | --- | --- |\n")
                for r in sec_rows:
                    f.write(f"{r}\n")
                f.write("\n")

        log.info("Logged job (source=%s) → %s", source, filepath)
        if source == "external":
            log_external_job_to_readme(job)
    except Exception as exc:
        log.error("Failed to write markdown log: %s", exc)


def log_external_job_to_readme(job) -> None:
    """Consolidates external application jobs in a dedicated README.md file in the daily log directory."""
    try:
        readme_path = os.path.join(DAILY_LOG_DIR, "README.md")
        today = datetime.now().strftime("%Y-%m-%d")
        time_str = datetime.now().strftime("%H:%M:%S")
        job_url  = (
            job.apply_link
            if hasattr(job, "apply_link") and job.apply_link
            else f"https://www.naukri.com/job-listings-{job.job_id}"
        )
        company = job.company.replace("|", "\\|").replace("\n", " ").strip()
        title   = job.title.replace("|", "\\|").replace("\n", " ").strip()

        new_row = f"| {today} | {time_str} | {company} | {title} | `{job.job_id}` | [View Job]({job_url}) |"

        lines = []
        if os.path.exists(readme_path):
            with open(readme_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            # Dedup check
            for line in lines:
                if f"`{job.job_id}`" in line:
                    return
        else:
            lines = [
                "# 🔗 Naukri External Jobs (Manual Review Required)\n\n",
                "This is a consolidated list of high-matching Data Engineering jobs that require manual application on external company websites.\n\n",
                "## 🔗 Pending Manual Applications\n\n",
                "| Date | Time | Company | Job Title | Job ID | Link |\n",
                "| --- | --- | --- | --- | --- | --- |\n"
            ]

        # Find the table header line to prepend below it
        insert_idx = -1
        for idx, line in enumerate(lines):
            if line.strip().startswith("| --- | --- |"):
                insert_idx = idx + 1
                break

        if insert_idx != -1:
            lines.insert(insert_idx, new_row + "\n")
        else:
            lines.append(new_row + "\n")

        with open(readme_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        log.info("Logged external job to README.md → %s", readme_path)
    except Exception as exc:
        log.error("Failed to log external job to README.md: %s", exc)


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


def search_tier(job_client: NaukriJobClient, keywords: list[str], seen_ids: set, job_age: int = 2) -> list:
    """
    Searches all keywords across 2 pages each. Returns unique jobs not already in
    seen_ids, ranked by skill relevance score (descending).
    job_age controls freshness window: 2 days on weekdays, 3 days on weekends.
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
                    job_age=job_age,
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
    Core apply loop. Searches all keywords in a single pass and applies until:
      - MAX_APPLIES_PER_RUN is reached, OR
      - Daily cap (MAX_APPLIES_PER_DAY) is reached

    New features vs. previous version:
      - Cross-day dedup: skips jobs already in applied_jobs_history.json
      - Weekend mode:    expands job_age to 3 days on Sat/Sun
      - Run stats:       logs evaluated/skipped/applied counts at the end

    Only applies to jobs that pass the strict relevance validation (is_job_relevant).
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

    # Expand search window on weekends (fewer fresh postings on Sat/Sun)
    is_weekend = datetime.now().weekday() >= 5   # 5=Saturday, 6=Sunday
    job_age    = 3 if is_weekend else 2
    if is_weekend:
        log.info("Weekend mode: expanding search window to %d days.", job_age)

    seen_ids           = set()
    applied_run        = 0
    skipped_dedup      = 0
    skipped_irrelevant = 0
    skipped_external   = 0

    log.info("─" * 55)
    log.info("Searching jobs for keywords: %s...", ", ".join(JOB_KEYWORDS))
    jobs = search_tier(job_client, JOB_KEYWORDS, seen_ids, job_age=job_age)
    log.info("Found %d unique candidate jobs after search", len(jobs))

    for job in jobs:
        if applied_run >= run_limit:
            break

        log.info("Evaluating: '%s' @ '%s'  (id=%s)", job.title, job.company, job.job_id)

        # 0. Cross-day duplicate check (applied_jobs_history.json)
        if is_already_applied(job.job_id):
            log.info("  → Skipping: Already applied previously (dedup).")
            skipped_dedup += 1
            continue

        # 1. Relevance check (strict title/skill check)
        relevant, reason = is_job_relevant(job)
        if not relevant:
            log.info("  → Skipping: Irrelevant title/skills (Reason: %s)", reason)
            skipped_irrelevant += 1
            continue

        # 2. Skip external apply jobs (log relevant ones for manual review)
        try:
            if job_client.is_external_apply(job.job_id):
                log.info("  → Skipping: External company apply — logged for manual review.")
                log_job_to_markdown(job, source="external")
                skipped_external += 1
                continue
        except Exception as exc:
            log.warning("  → Could not verify apply type (%s). Skipping.", exc)
            continue

        # 3. Apply
        success = apply_single_job(job_client, job)
        if success:
            log_job_to_markdown(job, source="search")
            increment_today_count(1)
            save_applied_history_entry(job)
            applied_run += 1

    log.info("─" * 55)
    log.info(
        "Run stats — Evaluated: %d | Dedup-skipped: %d | Irrelevant-skipped: %d | External-logged: %d | Applied: %d",
        len(jobs), skipped_dedup, skipped_irrelevant, skipped_external, applied_run,
    )
    log.info("Run complete. Applied: %d this run | %d total today (cap: %d).",
             applied_run, get_today_applied_count(), MAX_APPLIES_PER_DAY)
    return applied_run


def process_recommended_jobs(login_client: NaukriLoginClient) -> int:
    """
    Fetches Naukri's early-access ('pseudojobs') roles, checks keyword relevance,
    applies to matching ones, and logs them to the daily .md file.

    - Relevant jobs → apply_single_job (early_access) → logged under
      '## 👍 Share Interest (Recommended Jobs)'
    - Tier-1 external jobs → logged under '## 🔗 Manual Apply Required (Tier-1 External)'
    - Does NOT consume the daily apply cap (sharing interest ≠ applying).
    """
    log.info("Early access jobs run starting…")

    try:
        job_client = NaukriJobClient(login_client)
    except Exception as exc:
        log.error("Failed to init NaukriJobClient: %s", exc)
        return 0

    try:
        jobs = job_client.get_early_access_jobs()
        log.info("Fetched %d early access jobs.", len(jobs))
    except Exception as exc:
        log.error("Failed to fetch early access jobs: %s", exc)
        return 0

    seen_ids = load_recommended_seen_ids()
    shared_count = 0

    for job in jobs:
        if job.job_id in seen_ids:
            log.info("  Skipping already-seen job %s", job.job_id)
            continue
        relevant, reason = is_job_relevant(job)
        if not relevant:
            log.info("  Not relevant: '%s' (Reason: %s) (id=%s)", job.title, reason, job.job_id)
            continue

        log.info("Early access job relevant: '%s' @ '%s' (id=%s)",
                 job.title, job.company, job.job_id)
        # Early-access jobs are applied using the same apply endpoint but with early_access source.
        # They are always internal (company names hidden); no external URL to check.
        success = apply_single_job(job_client, job, run_source="early_access")
        if success:
            log_job_to_markdown(job, source="recommended")
            shared_count += 1
            save_recommended_seen_id(job.job_id)
            seen_ids.add(job.job_id)
        else:
            log.warning("  Apply failed for early access job %s — will retry next run.", job.job_id)

    log.info("Early access jobs run complete. Applied to %d early access jobs.", shared_count)
    return shared_count


def run_recommended_only() -> bool:
    """
    Lightweight run: login + check recommended jobs only.
    """
    log.info("=" * 55)
    log.info("RECOMMENDED JOBS RUN — %s", datetime.now().strftime("%Y-%m-%d %H:%M"))

    if not wait_for_internet():
        log.error("No internet. Aborting.")
        log.info("=" * 55)
        return False

    if not APPLY_JOBS:
        log.info("APPLY_JOBS is False in .env — nothing to do.")
        return True

    client = NaukriLoginClient(NAUKRI_USERNAME, NAUKRI_PASSWORD)
    try:
        client.login()
        log.info("Login successful")
    except NaukriClientError as exc:
        log.error("Login failed: %s", exc)
        return False

    process_recommended_jobs(client)

    log.info("Recommended jobs cycle complete ✓")
    log.info("=" * 55)
    return True


# ═══════════════════════════════════════════════════════════════════
# Full upload cycle (resume + headline rotation + apply)
# ═══════════════════════════════════════════════════════════════════

def upload_once() -> bool:
    """
    Full cycle: internet check → validate config → download resume →
    login → upload → headline (rotating) → (optional) apply.
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

    # ── headline update (rotating variants) ──────────────────────
    headline = get_next_headline()
    try:
        client.update_profile(headline=headline)
        log.info("Headline updated → '%s'  ✓", headline)
    except NaukriClientError as exc:
        log.warning("Headline update failed (non-fatal): %s", exc)

    # ── apply jobs ───────────────────────────────────────────────
    if APPLY_JOBS:
        apply_to_jobs(client)
        process_recommended_jobs(client)

    auto_mark_current_slot("full")

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
    process_recommended_jobs(client)

    auto_mark_current_slot("apply")

    log.info("Apply-only cycle complete ✓")
    log.info("=" * 55)
    return True



# ═══════════════════════════════════════════════════════════════════
# Weekly summary report
# ═══════════════════════════════════════════════════════════════════

def generate_weekly_summary() -> str:
    """
    Reads the past 7 daily markdown log files and produces a weekly summary with:
      - Per-day counts (applied / shared / external)
      - A consolidated list of all relevant external jobs still pending manual application.
    Returns the path of the generated summary file.
    """
    today = datetime.now()

    summary_rows:    list[str] = []
    all_external:    list[str] = []
    total_applied  = 0
    total_shared   = 0
    total_external = 0

    for i in range(6, -1, -1):   # oldest day first
        day     = today - timedelta(days=i)
        day_str = day.strftime("%Y-%m-%d")
        filepath = os.path.join(DAILY_LOG_DIR, f"{day_str}.md")

        if not os.path.exists(filepath):
            summary_rows.append(f"| {day_str} | — | — | — |")
            continue

        applied_count = shared_count = external_count = 0
        current_section = None

        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                ls = line.strip()
                if ls.startswith("## ✅"):
                    current_section = "applied"
                elif ls.startswith("## 👍"):
                    current_section = "shared"
                elif ls.startswith("## 🔗"):
                    current_section = "external"
                elif (
                    ls.startswith("| ")
                    and not ls.startswith("| Time")
                    and not ls.startswith("| ---")
                ):
                    if current_section == "applied":
                        applied_count += 1
                    elif current_section == "shared":
                        shared_count += 1
                    elif current_section == "external":
                        external_count += 1
                        all_external.append(f"  - [{day_str}] {ls}")

        total_applied  += applied_count
        total_shared   += shared_count
        total_external += external_count
        summary_rows.append(f"| {day_str} | {applied_count} | {shared_count} | {external_count} |")

    lines = [
        f"# Naukri Weekly Summary — Week ending {today.strftime('%d %b %Y')}",
        "",
        "## 📊 Weekly Overview",
        "",
        "| Date | ✅ Applied (Bot) | 👍 Shared Interest | 🔗 Manual Apply Pending |",
        "| --- | --- | --- | --- |",
        *summary_rows,
        "",
        f"**Totals: {total_applied} applied by bot | {total_shared} shared interest | {total_external} pending manual review**",
        "",
    ]

    if all_external:
        lines += [
            "## 🔗 All External Jobs Pending Manual Application",
            "",
            "These relevant Data Engineering roles require you to apply on the company website directly:",
            "",
            *all_external,
            "",
        ]
    else:
        lines += [
            "## 🔗 External Pending",
            "",
            "_No external jobs pending manual review this week._",
            "",
        ]

    os.makedirs(DAILY_LOG_DIR, exist_ok=True)
    summary_path = os.path.join(DAILY_LOG_DIR, f"weekly_summary_{today.strftime('%Y-%m-%d')}.md")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    log.info("Weekly summary written → %s", summary_path)
    return summary_path


def run_weekly_summary() -> bool:
    """Generates weekly summary report of job applications across the past 7 days."""
    log.info("=" * 55)
    log.info("WEEKLY SUMMARY — %s", datetime.now().strftime("%Y-%m-%d %H:%M"))
    try:
        path = generate_weekly_summary()
        log.info("Weekly summary complete ✓  → %s", path)
        log.info("=" * 55)
        return True
    except Exception as exc:
        log.error("Weekly summary failed: %s", exc)
        log.info("=" * 55)
        return False


# ═══════════════════════════════════════════════════════════════════
# Catch-up and Run History Tracking
# ═══════════════════════════════════════════════════════════════════

def mark_slot_completed(slot_time: str) -> None:
    today = _today_str()
    history = {"date": today, "completed_slots": []}
    if os.path.exists(RUN_HISTORY_FILE):
        try:
            with open(RUN_HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("date") == today:
                    history = data
        except Exception:
            pass
    if slot_time not in history["completed_slots"]:
        history["completed_slots"].append(slot_time)
    try:
        with open(RUN_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
        log.info("Marked scheduled slot %s as completed in history.", slot_time)
    except Exception as exc:
        log.error("Failed to save run history: %s", exc)


def auto_mark_current_slot(run_type: str) -> None:
    """
    Finds the closest scheduled slot of `run_type` to the current time (within 45 mins)
    and marks it completed in history.
    """
    now = datetime.now()
    best_slot = None
    min_diff = timedelta(minutes=45)

    for slot in SCHEDULED_SLOTS:
        if slot["type"] != run_type:
            continue
        try:
            slot_hour, slot_min = map(int, slot["time"].split(":"))
            slot_datetime = now.replace(hour=slot_hour, minute=slot_min, second=0, microsecond=0)
            diff = abs(now - slot_datetime)
            if diff < min_diff:
                min_diff = diff
                best_slot = slot["time"]
        except Exception:
            pass

    if best_slot:
        mark_slot_completed(best_slot)
    else:
        log.info("Unscheduled/manual %s run detected; not auto-marking slot.", run_type)


def catch_up_run() -> bool:
    """
    Checks all scheduled slots up to the current time. If any were missed today,
    runs them sequentially.
    """
    log.info("=" * 55)
    log.info("CATCH-UP CHECK — %s", datetime.now().strftime("%Y-%m-%d %H:%M"))

    # Quick internet check first. If no internet, exit immediately to be lightweight.
    try:
        r = requests.get("https://www.google.com", timeout=3)
        if r.status_code != 200:
            log.info("Internet not active during catch-up check. Exiting.")
            return False
    except Exception:
        log.info("Internet check failed during catch-up check. Exiting.")
        return False

    now = datetime.now()
    current_time_str = now.strftime("%H:%M")
    today = _today_str()

    completed_slots = []
    if os.path.exists(RUN_HISTORY_FILE):
        try:
            with open(RUN_HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("date") == today:
                    completed_slots = data.get("completed_slots", [])
        except Exception:
            pass

    missed_slots = []
    for slot in SCHEDULED_SLOTS:
        if slot["time"] <= current_time_str:
            if slot["time"] not in completed_slots:
                missed_slots.append(slot)

    if not missed_slots:
        log.info("All scheduled slots up to %s are already completed.", current_time_str)
        log.info("=" * 55)
        return True

    log.info("Found %d missed slots today: %s", len(missed_slots), [s["time"] for s in missed_slots])

    for slot in missed_slots:
        slot_time = slot["time"]
        slot_type = slot["type"]

        log.info("Running catch-up for slot %s (%s)...", slot_time, slot_type)
        if slot_type == "full":
            success = upload_once()
        else:
            success = apply_only_run()

        if success:
            mark_slot_completed(slot_time)
            # Pause briefly if there are more catch-ups to run
            if slot != missed_slots[-1]:
                log.info("Pausing 30 seconds before next caught-up run...")
                time.sleep(30)
        else:
            log.error("Catch-up run failed for slot %s. Stopping catch-up queue.", slot_time)
            break

    log.info("Catch-up cycle complete.")
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
        help="Full run: resume upload + headline rotation + apply (used at 9:15 AM and 2:00 PM)",
    )
    group.add_argument(
        "--apply-only",
        action="store_true",
        dest="apply_only",
        help="Apply-only run: skip resume upload (used at 11:15, 13:15, 16:15)",
    )
    group.add_argument(
        "--recommended",
        action="store_true",
        dest="recommended",
        help="Recommended jobs run: check and apply to recommended jobs, log relevant external ones",
    )
    group.add_argument(
        "--weekly-summary",
        action="store_true",
        dest="weekly_summary",
        help="Generate a weekly summary report of all job applications from the past 7 days",
    )
    group.add_argument(
        "--catch-up",
        action="store_true",
        dest="catch_up",
        help="Catch-up check: run any missed scheduled runs for today",
    )
    args = parser.parse_args()

    if args.now:
        success = upload_once()
        sys.exit(0 if success else 1)
    elif args.apply_only:
        success = apply_only_run()
        sys.exit(0 if success else 1)
    elif args.recommended:
        success = run_recommended_only()
        sys.exit(0 if success else 1)
    elif args.weekly_summary:
        success = run_weekly_summary()
        sys.exit(0 if success else 1)
    elif args.catch_up:
        success = catch_up_run()
        sys.exit(0 if success else 1)
    else:
        run_scheduler()
