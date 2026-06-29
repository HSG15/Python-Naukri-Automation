import sys
import os
import time
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

sys.path.insert(0, "/Users/harishankargiri/MyProject/Vibe Coding/Naukri-UpdateResume")
load_dotenv("/Users/harishankargiri/MyProject/Vibe Coding/Naukri-UpdateResume/.env")

from src.client.naukri_client import NaukriLoginClient

NAUKRI_USERNAME = os.getenv("NAUKRI_USERNAME", "")
NAUKRI_PASSWORD = os.getenv("NAUKRI_PASSWORD", "")

print(f"Logging in as {NAUKRI_USERNAME} via API...")
client = NaukriLoginClient(NAUKRI_USERNAME, NAUKRI_PASSWORD)
try:
    client.login()
    print("API Login successful!")
    
    print("Launching Chrome via Selenium...")
    options = Options()
    # options.add_argument("--start-maximized")
    driver = webdriver.Chrome(options=options)
    
    # Go to Naukri to set domain
    driver.get("https://www.naukri.com/mnjuser/homepage")
    time.sleep(2)
    
    # Inject cookies
    print("Injecting cookies into browser...")
    for cookie in client.session.cookies:
        cookie_dict = {
            'name': cookie.name,
            'value': cookie.value,
            'domain': cookie.domain,
            'path': cookie.path,
        }
        if cookie.expires:
            cookie_dict['expiry'] = cookie.expires
        try:
            driver.add_cookie(cookie_dict)
        except Exception as e:
            # Some cookies might fail if domain mismatch (e.g. google analytics, doubleclick)
            pass
            
    print("Navigating to profile page...")
    driver.get("https://www.naukri.com/mnjuser/profile")
    time.sleep(3)
    
    print("\n" + "="*80)
    print("SUCCESS: Browser is open and logged in!")
    print("You can now update the following sections in the browser window:")
    print("1. Rename Projects:")
    print("   - Change 'Real Time Job Market Analytics Platform' to:")
    print("     'Real-Time Data Engineering Pipeline using AWS Lambda, AWS Glue, Spark, Athena, S3, ETL'")
    print("   - Change 'AI-Native E-commerce Data Pipeline' to:")
    print("     'AI-Native E-commerce Data Pipeline using Databricks, PySpark, ADF, ADLS Gen2, Qdrant, VectorDB'")
    print("2. Expand Key Skills / IT Skills:")
    print("   - Add missing ones: PySpark, Databricks, Delta Lake, ADF, AWS Glue, AWS Lambda, Synapse, Airflow, Qdrant, Vector Database, Medallion Architecture")
    print("3. Check Employment details at Blue Flame Labs (ensure ETL/ELT, Azure, PySpark are prominently listed).")
    print("="*80 + "\n")
    
    input("Press ENTER here in the terminal once you have finished editing, to close the browser session... ")
    driver.quit()
    print("Browser closed.")
except Exception as e:
    print(f"Error: {e}")
