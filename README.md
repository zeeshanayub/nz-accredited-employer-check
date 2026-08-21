# SEEK Accreditation Check

Checks your Gmail inbox for unread **SEEK Recommendations** emails, extracts
every job listed in them, cross-checks each employer against New Zealand's
[Accredited Employer list](https://www.immigration.govt.nz/work/requirements-for-work-visas/approved-employers/accredited-employer-list/),
and emails you a summary report. Runs daily on a schedule via GitHub Actions,
or on demand from the command line.

## How it works

```
check_seek_emails.py          Find unread "SEEK Recommendations" emails,
                               extract job title / company / link from each,
                               save originals as .eml/.html for reference.
        │
        ▼
check_accreditation.py        Look up each company on the NZ Immigration
(via accredited_employer_api) Accredited Employer list. If a name contains
                               "Ltd" and isn't found, retries as "Limited".
        │
        ▼
send_accreditation_report.py  Email a summary (accredited jobs first, with
                               matched employer name + expiry date), then
                               mark the original SEEK email(s) as read.
```

`run_pipeline.py` runs all three in order. Each step is a safe no-op if
there's nothing new to process — running it daily with no new SEEK email
just prints a short message and exits, nothing breaks.

## Repo layout

| File | Purpose |
|---|---|
| `check_seek_emails.py` | Gmail API auth, fetch + parse SEEK emails, extract job listings |
| `accredited_employer_api.py` | Thin client for the NZ Immigration Accredited Employer list API |
| `check_accreditation.py` | Cross-checks extracted jobs' companies against that list |
| `send_accreditation_report.py` | Builds and sends the summary email, marks source email(s) read |
| `run_pipeline.py` | Runs all three steps in sequence |
| `.github/workflows/daily-seek-check.yml` | Daily schedule (10:00 AM PKT) via GitHub Actions |

Generated output (`seek_jobs.jsonl`, `seek_accreditation_check.json`,
`seek_snippets.txt`, `seek_emails_eml/`, `seek_emails_html/`) is gitignored —
each run overwrites it fresh, nothing accumulates.

## Setup

### 1. Gmail API access

1. Create a project in the [Google Cloud Console](https://console.cloud.google.com/projectcreate).
2. Enable the [Gmail API](https://console.cloud.google.com/apis/library/gmail.googleapis.com) for it.
3. Configure the [OAuth consent screen](https://console.cloud.google.com/apis/credentials/consent):
   - User type: External
   - Add yourself as a test user
   - **Publish the app** (Publish App button) — otherwise refresh tokens expire
     after 7 days and any automation quietly breaks weekly.
4. Create an [OAuth Client ID](https://console.cloud.google.com/apis/credentials) → Application type: **Desktop app** → download the JSON.
5. Save it as `credentials.json` in this directory.

### 2. Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. First run (local)

```bash
echo -n "you@example.com" > report_to.txt   # where reports get emailed
python run_pipeline.py
```

The first run opens a browser for you to grant Gmail access (read/modify/send)
and saves the result to `token.json`. After that, it runs unattended.

## Running it daily via GitHub Actions

The included workflow (`.github/workflows/daily-seek-check.yml`) runs the
pipeline every day at 10:00 AM PKT, plus supports manual triggering from the
Actions tab.

Add these as repo secrets (Settings → Secrets and variables → Actions):

| Secret | Value |
|---|---|
| `GMAIL_TOKEN_JSON` | contents of your local `token.json` |
| `GMAIL_CREDENTIALS_JSON` | contents of your local `credentials.json` |
| `REPORT_TO_EMAIL` | the email address reports should go to |

Runs on GitHub's free tier (Linux runner minutes) — a daily run of this
pipeline uses roughly 1-2 minutes, well within the free monthly quota.

## Caveats

- **Employer name matching isn't exact.** The immigration site's search
  sometimes matches a job listing's short/trading name to a differently-named
  legal entity (e.g. "Tait Communications" → "TAIT INTERNATIONAL LIMITED").
  Always check the `matched_employer_name` field before relying on a result.
- **Never commit `credentials.json`, `token.json`, or `report_to.txt`** —
  all three are gitignored on purpose; they grant Gmail account access or
  reveal your personal email address.
