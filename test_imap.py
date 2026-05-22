from imap_tools import MailBox
import re

app_password = "ktmz uigj apaw fqdb"
username = "hsgiri.data@gmail.com"
try:
    with MailBox('imap.gmail.com').login(username, app_password) as mailbox:
        for msg in mailbox.fetch(limit=15, reverse=True):
            sender = msg.from_.lower()
            print(f"From: {msg.from_}, Subject: {msg.subject}")
            if 'naukri' in sender or 'infoedge' in sender:
                text = (msg.subject or "") + " " + (msg.text or "") + " " + (msg.html or "")
                matches = re.findall(r'\b\d{6}\b', text)
                print(f"Found matches: {matches}")
except Exception as e:
    print(f"Error: {e}")
