from src.client.naukri_client import NaukriLoginClient
import os
from dotenv import load_dotenv
load_dotenv()
username = os.getenv("NAUKRI_USERNAME")
password = os.getenv("NAUKRI_PASSWORD")
app_password = os.getenv("GMAIL_APP_PASSWORD")

client = NaukriLoginClient(username, password)

print("Triggering OTP via send_otp...")
res = client.send_otp(is_mobile=False)
print("Send OTP Response:", res)

import time
print("Waiting 15 seconds for email...")
time.sleep(15)

otp = client._fetch_otp_from_gmail(app_password)
print(f"Fetched OTP: {otp}")

if otp:
    print("Verifying OTP...")
    try:
        session = client.verify_otp(otp, is_mobile=False)
        print("Success! Bearer token:", session.bearer_token[:20] + "...")
    except Exception as e:
        print("Failed to verify:", e)
