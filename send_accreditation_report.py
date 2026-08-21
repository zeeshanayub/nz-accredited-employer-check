"""
Email a summary of seek_accreditation_check.json to REPORT_TO, then mark the
original SEEK recommendation email(s) it was built from as read.
"""

import base64
import json
import os
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from googleapiclient.errors import HttpError

from check_seek_emails import SCRIPT_DIR, get_gmail_service, mark_as_read

RESULTS_FILE = os.path.join(SCRIPT_DIR, "seek_accreditation_check.json")
REPORT_TO_FILE = os.path.join(SCRIPT_DIR, "report_to.txt")


def get_report_to():
    """Destination address — from REPORT_TO_EMAIL env var (CI), or a local gitignored file."""
    env_value = os.environ.get("REPORT_TO_EMAIL")
    if env_value:
        return env_value

    if os.path.exists(REPORT_TO_FILE):
        with open(REPORT_TO_FILE, "r", encoding="utf-8") as f:
            value = f.read().strip()
        if value:
            return value

    sys.exit(
        "No report destination configured.\n"
        f"Set the REPORT_TO_EMAIL environment variable, or create {REPORT_TO_FILE} "
        "containing the destination email address."
    )


def load_results(results_file=RESULTS_FILE):
    if not os.path.exists(results_file):
        return []
    with open(results_file, "r", encoding="utf-8") as f:
        return json.load(f)


def build_report_email(results, to_addr):
    accredited = [r for r in results if r["accredited"]]
    not_found = [r for r in results if not r["accredited"]]
    subject = f"SEEK Accreditation Check — {len(accredited)}/{len(results)} accredited"

    text_lines = [f"SEEK job recommendations — accreditation check ({len(results)} jobs)", ""]
    text_lines.append(f"ACCREDITED ({len(accredited)}):")
    for r in accredited:
        text_lines.append(f"- {r['job_title']} @ {r['company']}")
        text_lines.append(f"  Matched: {r['matched_employer_name']} (expires {r['accreditation_expiry']})")
        text_lines.append(f"  {r['job_link']}")
    text_lines.append("")
    text_lines.append(f"NOT FOUND ({len(not_found)}):")
    for r in not_found:
        text_lines.append(f"- {r['job_title']} @ {r['company']}")
        text_lines.append(f"  {r['job_link']}")
    text_body = "\n".join(text_lines)

    def html_row(r, ok):
        badge = "&#9989; Accredited" if ok else "&#10060; Not found"
        detail = (
            f"Matched: {r['matched_employer_name']}<br>Expires: {r['accreditation_expiry']}"
            if ok
            else "&mdash;"
        )
        return f"""<tr>
            <td style="padding:8px;border-bottom:1px solid #eee">{r['job_title']}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">{r['company']}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">{badge}</td>
            <td style="padding:8px;border-bottom:1px solid #eee;font-size:12px;color:#555">{detail}</td>
            <td style="padding:8px;border-bottom:1px solid #eee"><a href="{r['job_link']}">View job</a></td>
        </tr>"""

    rows = "".join(html_row(r, True) for r in accredited) + "".join(
        html_row(r, False) for r in not_found
    )

    html_body = f"""<html><body style="font-family:Arial,sans-serif;color:#222">
        <h2>SEEK Accreditation Check</h2>
        <p>{len(accredited)} of {len(results)} recommended jobs are at accredited employers.</p>
        <table style="border-collapse:collapse;width:100%;font-size:14px">
            <thead>
                <tr style="text-align:left;background:#f5f5f5">
                    <th style="padding:8px">Job Title</th>
                    <th style="padding:8px">Company</th>
                    <th style="padding:8px">Status</th>
                    <th style="padding:8px">Detail</th>
                    <th style="padding:8px">Link</th>
                </tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>
        <p style="font-size:12px;color:#888">
            Note: employer name matching against the Immigration NZ list is not exact —
            double-check "Matched" names before relying on a result.
        </p>
    </body></html>"""

    msg = MIMEMultipart("alternative")
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))
    return msg


def send_email(service, mime_message):
    raw = base64.urlsafe_b64encode(mime_message.as_bytes()).decode("utf-8")
    return service.users().messages().send(userId="me", body={"raw": raw}).execute()


def main():
    results = load_results()
    if not results:
        print("No accreditation results to report.")
        return

    report_to = get_report_to()
    service = get_gmail_service()

    mime_message = build_report_email(results, report_to)
    sent = send_email(service, mime_message)
    print(f"Report emailed (message id: {sent['id']})")

    email_ids = sorted({r["email_id"] for r in results if r.get("email_id")})
    for email_id in email_ids:
        mark_as_read(service, email_id)
        print(f"Marked original email {email_id} as read")


if __name__ == "__main__":
    try:
        main()
    except HttpError as error:
        sys.exit(f"Gmail API error: {error}")
