"""
Run the full pipeline in one go:
  1. check_seek_emails.py    — fetch unread SEEK Recommendations emails, extract jobs
  2. check_accreditation.py  — cross-check each company against the Accredited Employer list
  3. send_accreditation_report.py — email the results, mark the source email(s) as read

Each step is a no-op (prints a message, does nothing else) if there's nothing
new to process, so this is safe to run daily even with no new SEEK email.
"""

from check_accreditation import main as check_accreditation
from check_seek_emails import main as check_seek_emails
from send_accreditation_report import main as send_report


def main():
    print("=== Step 1/3: Checking Gmail for new SEEK Recommendations ===")
    check_seek_emails()

    print("\n=== Step 2/3: Checking employers against the Accredited Employer list ===")
    check_accreditation()

    print("\n=== Step 3/3: Emailing the report ===")
    send_report()


if __name__ == "__main__":
    main()
