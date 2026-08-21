"""
Check Gmail for unread "SEEK Recommendations" emails.

Looks for unread messages from noreply@s.seek.co.nz whose sender display
name contains "SEEK Recommendations", using the Gmail API.

Setup required before running — see README_gmail_setup.md.
"""

import base64
import html
import json
import os
import re
import sys
from datetime import datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from bs4 import BeautifulSoup
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# modify: list/read messages + mark as read (remove UNREAD label).
# send: send the accreditation report email.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(SCRIPT_DIR, "credentials.json")
TOKEN_FILE = os.path.join(SCRIPT_DIR, "token.json")
SNIPPETS_FILE = os.path.join(SCRIPT_DIR, "seek_snippets.txt")
EML_DIR = os.path.join(SCRIPT_DIR, "seek_emails_eml")
HTML_DIR = os.path.join(SCRIPT_DIR, "seek_emails_html")
JOBS_FILE = os.path.join(SCRIPT_DIR, "seek_jobs.jsonl")

SENDER_EMAIL = "noreply@s.seek.co.nz"
SENDER_NAME_MATCH = "SEEK Recommendations"

# Gmail search query: unread + from this address.
# (Gmail search doesn't let us filter on the display name directly, so we
# double-check that part in Python after fetching the messages.)
GMAIL_QUERY = f'is:unread from:{SENDER_EMAIL}'


def _materialize_from_env():
    """In CI (no browser available), write credentials.json/token.json from env vars.

    Leaves local files alone if they already exist so this has no effect when
    running interactively on a machine that's already been through the OAuth flow.
    """
    if not os.path.exists(CREDENTIALS_FILE) and os.environ.get("GMAIL_CREDENTIALS_JSON"):
        with open(CREDENTIALS_FILE, "w") as f:
            f.write(os.environ["GMAIL_CREDENTIALS_JSON"])

    if not os.path.exists(TOKEN_FILE) and os.environ.get("GMAIL_TOKEN_JSON"):
        with open(TOKEN_FILE, "w") as f:
            f.write(os.environ["GMAIL_TOKEN_JSON"])


def get_gmail_service():
    """Authenticate with the Gmail API, refreshing/creating token.json as needed."""
    _materialize_from_env()
    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                sys.exit(
                    f"Missing {CREDENTIALS_FILE}.\n"
                    "Download OAuth client credentials from Google Cloud Console "
                    "and save them as credentials.json next to this script.\n"
                    "See README_gmail_setup.md for the full setup steps."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def mark_as_read(service, msg_id):
    """Remove the UNREAD label from a message."""
    service.users().messages().modify(
        userId="me", id=msg_id, body={"removeLabelIds": ["UNREAD"]}
    ).execute()


def get_header(headers, name):
    for header in headers:
        if header["name"].lower() == name.lower():
            return header["value"]
    return ""


def decode_snippet(message):
    return message.get("snippet", "")


class _TextExtractor(HTMLParser):
    """Minimal HTML-to-text converter: keeps text, adds newlines at block tags."""

    BLOCK_TAGS = {"br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4"}

    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)

    def get_text(self):
        return "".join(self.parts)


def html_to_text(html_content):
    parser = _TextExtractor()
    parser.feed(html_content)
    text = html.unescape(parser.get_text())
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def decode_body_data(data):
    if not data:
        return ""
    decoded = base64.urlsafe_b64decode(data.encode("UTF-8"))
    return decoded.decode("utf-8", errors="replace")


def get_message_parts(payload):
    """Walk the MIME parts and return (plain_text, html_text); either may be empty."""
    plain_text = None
    html_text = None

    def walk(part):
        nonlocal plain_text, html_text
        mime_type = part.get("mimeType", "")
        data = part.get("body", {}).get("data")

        if mime_type == "text/plain" and data and plain_text is None:
            plain_text = decode_body_data(data)
        elif mime_type == "text/html" and data and html_text is None:
            html_text = decode_body_data(data)

        for sub_part in part.get("parts", []) or []:
            walk(sub_part)

    walk(payload)
    return (plain_text or "").strip(), (html_text or "").strip()


def fetch_raw_eml(service, msg_id):
    """Fetch the message exactly as Gmail stored it (full RFC 822 source)."""
    raw_msg = service.users().messages().get(userId="me", id=msg_id, format="raw").execute()
    raw_data = raw_msg.get("raw", "")
    padded = raw_data + "=" * (-len(raw_data) % 4)
    return base64.urlsafe_b64decode(padded)


def sanitize_filename(text, max_length=80):
    text = re.sub(r"[^\w\-. ]", "_", text).strip()
    return text[:max_length] or "email"


def save_raw_eml(service, msg_id, subject, date):
    """Save the original email (headers + MIME structure intact) as a .eml file."""
    os.makedirs(EML_DIR, exist_ok=True)
    raw_bytes = fetch_raw_eml(service, msg_id)

    try:
        date_prefix = parsedate_to_datetime(date).strftime("%Y%m%d_%H%M")
    except (TypeError, ValueError):
        date_prefix = sanitize_filename(date, max_length=20)

    filename = f"{date_prefix}_{sanitize_filename(subject, max_length=50)}_{msg_id}.eml"
    filepath = os.path.join(EML_DIR, filename)

    with open(filepath, "wb") as f:
        f.write(raw_bytes)

    return filepath


def save_html(subject, date, msg_id, html_content):
    """Save the email's raw text/html MIME part as-is (tags, links, structure intact)."""
    os.makedirs(HTML_DIR, exist_ok=True)

    try:
        date_prefix = parsedate_to_datetime(date).strftime("%Y%m%d_%H%M")
    except (TypeError, ValueError):
        date_prefix = sanitize_filename(date, max_length=20)

    filename = f"{date_prefix}_{sanitize_filename(subject, max_length=50)}_{msg_id}.html"
    filepath = os.path.join(HTML_DIR, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html_content)

    return filepath


def extract_job_listings(html_content):
    """Pull {title, company, link} for each job card out of a SEEK recommendations email.

    Each job card is wrapped in an <a> tag; the underlined title sits in a
    <div style="text-decoration:underline">, and the company name is the next
    row down. This holds whether or not the employer has a logo.
    """
    soup = BeautifulSoup(html_content, "html.parser")
    jobs = []
    seen_links = set()

    for a in soup.find_all("a", href=True):
        title_div = a.find("div", style=lambda s: s and "text-decoration:underline" in s)
        if not title_div:
            continue

        title = title_div.get_text(strip=True)
        title_td = title_div.find_parent("td")
        if not title_td:
            continue

        company_tr = title_td.find_parent("tr").find_next_sibling("tr")
        company = company_tr.get_text(strip=True) if company_tr else ""

        link = a["href"]
        if not title or link in seen_links:
            continue
        seen_links.add(link)

        jobs.append({"title": title, "company": company, "link": link})

    return jobs


def build_job_records(jobs, email_meta):
    """Tag extracted job listings with their source email + fetch time."""
    fetched_at = datetime.now().isoformat(timespec="seconds")
    return [
        {
            "fetched_at": fetched_at,
            "email_id": email_meta["id"],
            "email_subject": email_meta["subject"],
            "email_date": email_meta["date"],
            "job_title": job["title"],
            "company": job["company"],
            "job_link": job["link"],
        }
        for job in jobs
    ]


def save_jobs(job_records, output_file=JOBS_FILE):
    """Write this run's job listings as JSON lines, replacing any previous run's file."""
    with open(output_file, "w", encoding="utf-8") as f:
        for record in job_records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def find_unread_seek_recommendations(service, max_results=25):
    """Return unread emails from SEEK where the sender name matches."""
    matches = []
    page_token = None

    while True:
        response = (
            service.users()
            .messages()
            .list(
                userId="me",
                q=GMAIL_QUERY,
                maxResults=max_results,
                pageToken=page_token,
            )
            .execute()
        )

        for msg_meta in response.get("messages", []):
            message = (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=msg_meta["id"],
                    format="full",
                )
                .execute()
            )

            payload = message.get("payload", {})
            headers = payload.get("headers", [])
            from_header = get_header(headers, "From")

            if SENDER_NAME_MATCH.lower() in from_header.lower():
                plain_text, html_text = get_message_parts(payload)
                matches.append(
                    {
                        "id": message["id"],
                        "threadId": message["threadId"],
                        "from": from_header,
                        "subject": get_header(headers, "Subject"),
                        "date": get_header(headers, "Date"),
                        "snippet": decode_snippet(message),
                        "body": plain_text or html_to_text(html_text),
                        "html": html_text,
                    }
                )

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return matches


def save_snippets_to_file(matches, output_file=SNIPPETS_FILE):
    """Write each match's full body text (with subject/date), replacing any previous run's file."""
    run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(f"=== Checked {run_time} — {len(matches)} unread ===\n\n")
        for m in matches:
            f.write(f"Subject: {m['subject']}\n")
            f.write(f"Date:    {m['date']}\n")
            f.write(f"From:    {m['from']}\n")
            f.write("-" * 60 + "\n")
            f.write(f"{m['body']}\n")
            f.write("=" * 60 + "\n\n")


def main():
    service = get_gmail_service()
    matches = find_unread_seek_recommendations(service)

    if not matches:
        print("No unread SEEK Recommendations emails found.")
        return

    print(f"Found {len(matches)} unread SEEK Recommendations email(s):\n")
    for m in matches:
        print(f"- Subject: {m['subject']}")
        print(f"  From:    {m['from']}")
        print(f"  Date:    {m['date']}")
        print(f"  Snippet: {m['snippet']}")
        print(f"  Link:    https://mail.google.com/mail/u/0/#inbox/{m['id']}")
        print()

    save_snippets_to_file(matches)
    print(f"Snippets saved to {SNIPPETS_FILE}")

    all_job_records = []

    for m in matches:
        eml_path = save_raw_eml(service, m["id"], m["subject"], m["date"])
        print(f"Original email saved to {eml_path}")

        if m["html"]:
            html_path = save_html(m["subject"], m["date"], m["id"], m["html"])
            print(f"HTML body saved to {html_path}")

            jobs = extract_job_listings(m["html"])
            if jobs:
                print(f"\n{len(jobs)} job(s) extracted from \"{m['subject']}\":")
                for job in jobs:
                    print(f"  - {job['title']} — {job['company']}")
                    print(f"    {job['link']}")
                all_job_records.extend(build_job_records(jobs, m))

    if all_job_records:
        save_jobs(all_job_records)
        print(f"\nJob listings saved to {JOBS_FILE}")


if __name__ == "__main__":
    try:
        main()
    except HttpError as error:
        sys.exit(f"Gmail API error: {error}")
