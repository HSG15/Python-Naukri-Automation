#!/usr/bin/env python3
"""
save_session.py — Save Naukri browser session token (nauk_at) to bypass reCAPTCHA.

How to get your 'nauk_at' token from your browser:
  1. Open https://www.naukri.com (ensure you are logged in).
  2. Press Cmd + Option + I (Inspect) -> Click the 'Application' (or 'Storage') tab.
  3. Expand 'Cookies' -> Click 'https://www.naukri.com'.
  4. Find the cookie named 'nauk_at' and copy its entire 'Value'.
  5. Run:
       venv/bin/python save_session.py "<your_token_here>" --upload

Usage:
  venv/bin/python save_session.py <token>             # Save and verify token
  venv/bin/python save_session.py <token> --upload    # Save, verify, and run full profile update
  venv/bin/python save_session.py                    # Interactive prompt
"""

import sys
import os
import json
import argparse
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

SESSION_FILE = os.path.join(PROJECT_ROOT, "naukri_session.json")

def clean_token(token: str) -> str:
    token = token.strip()
    if (token.startswith('"') and token.endswith('"')) or (token.startswith("'") and token.endswith("'")):
        token = token[1:-1].strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token

def save_and_verify(token: str, run_upload: bool = False):
    token = clean_token(token)
    if not token:
        print("\n❌ Error: Empty token provided.")
        return False

    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump({"nauk_at": token}, f, indent=2)
    print(f"\n💾 Saved session token to {os.path.basename(SESSION_FILE)}")

    print("🔍 Verifying session with Naukri...")
    from src.client.naukri_client import NaukriLoginClient
    client = NaukriLoginClient("session_user", "session_user")
    try:
        session = client.login()
        if not session:
            print("❌ Verification failed: Could not establish session.")
            return False

        pid = client.fetch_profile_id()
        print(f"✅ Session verified successfully!")
        print(f"   Profile ID: {pid}")
    except Exception as exc:
        print(f"❌ Session verification failed: {exc}")
        print("   Please ensure you copied the full 'nauk_at' cookie value while logged in.")
        return False

    if run_upload:
        print("\n🚀 Running full profile update now...")
        import subprocess
        python_bin = os.path.join(PROJECT_ROOT, "venv", "bin", "python")
        if not os.path.exists(python_bin):
            python_bin = sys.executable
        daily_script = os.path.join(PROJECT_ROOT, "daily_upload.py")
        subprocess.run([python_bin, daily_script, "--now"])

    return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Save Naukri session token")
    parser.add_argument("token", nargs="?", default="", help="The 'nauk_at' cookie value")
    parser.add_argument("--upload", action="store_true", help="Immediately run full profile update after verifying")
    args = parser.parse_args()

    token = args.token
    if not token:
        print("=" * 60)
        print("Naukri Session Token Saver")
        print("=" * 60)
        print("In your browser (where you are logged in to Naukri):")
        print("1. Open DevTools (Cmd+Option+I) -> Application -> Cookies -> naukri.com")
        print("2. Copy the Value of 'nauk_at'\n")
        try:
            token = input("Paste your 'nauk_at' token: ")
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            sys.exit(1)

    success = save_and_verify(token, run_upload=args.upload)
    sys.exit(0 if success else 1)

