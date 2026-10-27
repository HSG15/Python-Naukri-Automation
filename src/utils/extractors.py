
import re

def extract_form_key2(js_text: str) -> str | None:
    # Pattern 1 (post-Sep-2026 update): this.formKey="F75f8e7h84e205"
    match = re.search(r'this\.formKey\s*=\s*"([A-Za-z0-9]{8,})"', js_text)
    if match:
        return match.group(1)

    # Pattern 2: formKey: "..." or formKey = "..." (any quote style)
    match = re.search(r'formKey\s*[:=]\s*["\x27]([A-Za-z0-9]{8,})["\x27]', js_text)
    if match:
        return match.group(1)

    # Pattern 3 (legacy, pre-Sep-2026): key:"initUploader" ... d = "..."
    match = re.search(r'key:"initUploader".*?d\s*=\s*"([A-Za-z0-9]+)"', js_text, re.DOTALL)
    if match:
        return match.group(1)

    # Pattern 4 (legacy generic fallback): d = "<10+ alphanumeric chars>"
    match = re.search(r'd\s*=\s*"([A-Za-z0-9]{10,})"', js_text)
    return match.group(1) if match else None

def extract_all_js_urls(html: str):
    return re.findall(r'<script[^>]+src="([^"]*\.js[^"]*)"', html)
