"""
Cross-check SEEK job listings (seek_jobs.jsonl) against the NZ Immigration
Accredited Employer list, using accredited_employer_api.check_employer_accredited().

If a company name contains "Ltd" and isn't found, retries once with "Ltd"
replaced by "Limited" (the immigration site's search wants the full form).
"""

import csv
import hashlib
import json
import os
import re
import time
from datetime import datetime

from accredited_employer_api import check_employer_accredited

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
JOBS_FILE = os.path.join(SCRIPT_DIR, "seek_jobs.jsonl")
RESULTS_FILE = os.path.join(SCRIPT_DIR, "seek_accreditation_check.json")
PROCESSED_JOBS_CSV = os.path.join(SCRIPT_DIR, "processed_jobs.csv")

CSV_FIELDS = [
    "job_id",
    "first_seen",
    "job_title",
    "company",
    "accredited",
    "matched_employer_name",
    "accreditation_expiry",
    "email_id",
    "email_subject",
]

LTD_PATTERN = re.compile(r"\bLtd\.?\b", re.IGNORECASE)


def make_job_id(title, company):
    """Stable ID for a job, independent of SEEK's per-email tracking links.

    SEEK wraps the same job's link differently in every email it's mentioned
    in, so the link can't be used as an identity — hash the normalized
    title+company instead.
    """
    key = f"{title.strip().lower()}|{company.strip().lower()}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def load_processed_job_ids(csv_file=PROCESSED_JOBS_CSV):
    if not os.path.exists(csv_file):
        return set()

    with open(csv_file, "r", encoding="utf-8", newline="") as f:
        return {row["job_id"] for row in csv.DictReader(f)}


def append_processed_jobs(rows, csv_file=PROCESSED_JOBS_CSV):
    if not rows:
        return

    file_exists = os.path.exists(csv_file)
    with open(csv_file, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


def load_jobs(jobs_file=JOBS_FILE):
    if not os.path.exists(jobs_file):
        return []

    jobs = []
    with open(jobs_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                jobs.append(json.loads(line))
    return jobs


def parse_match(data):
    """Pull the matched employer name + accreditation expiry from an API response."""
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list) or not results:
        return None, None

    first = results[0]
    matched_name = first.get("title", {}).get("raw")

    expiry = None
    for field in first.get("field_schema", {}).get("raw", []):
        if field.get("APIColumn") == "expiryDateOfAccreditation":
            expiry = field.get("Value")
            break

    return matched_name, expiry


def check_company(company, cache):
    """Look up a company name, retrying 'Ltd' -> 'Limited' if the first search misses."""
    if company in cache:
        return cache[company]

    matched_name, expiry = parse_match(check_employer_accredited(company))
    query_used = company

    if not matched_name and LTD_PATTERN.search(company):
        retry_query = LTD_PATTERN.sub("Limited", company)
        matched_name, expiry = parse_match(check_employer_accredited(retry_query))
        query_used = retry_query

    result = {
        "accredited": matched_name is not None,
        "matched_employer_name": matched_name,
        "accreditation_expiry": expiry,
        "query_used": query_used,
    }
    cache[company] = result
    return result


def main():
    jobs = load_jobs()
    if not jobs:
        print(f"No jobs found in {JOBS_FILE}.")
        # Overwrite any stale results from a previous run — otherwise
        # send_accreditation_report.py would re-send an old report.
        with open(RESULTS_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)
        return

    seen_job_ids = load_processed_job_ids()
    cache = {}
    output = []
    new_csv_rows = []
    skipped = 0

    for job in jobs:
        company = job.get("company", "").strip()
        title = job.get("job_title", "").strip()
        if not company or not title:
            continue

        job_id = make_job_id(title, company)
        if job_id in seen_job_ids:
            skipped += 1
            continue
        seen_job_ids.add(job_id)  # also dedupes repeats within this same run

        is_new_company = company not in cache
        result = check_company(company, cache)
        job_with_id = {**job, "job_id": job_id}
        output.append({**job_with_id, **result})

        status = "ACCREDITED" if result["accredited"] else "not found"
        detail = f' (as "{result["matched_employer_name"]}")' if result["accredited"] else ""
        print(f"- {title} @ {company}: {status}{detail}")

        new_csv_rows.append(
            {
                "job_id": job_id,
                "first_seen": datetime.now().isoformat(timespec="seconds"),
                "job_title": title,
                "company": company,
                "accredited": result["accredited"],
                "matched_employer_name": result["matched_employer_name"] or "",
                "accreditation_expiry": result["accreditation_expiry"] or "",
                "email_id": job.get("email_id", ""),
                "email_subject": job.get("email_subject", ""),
            }
        )

        if is_new_company:
            time.sleep(0.5)

    if skipped:
        print(f"\nSkipped {skipped} job(s) already seen in a previous run.")

    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    append_processed_jobs(new_csv_rows)

    accredited_count = sum(1 for r in output if r["accredited"])
    print(f"\n{accredited_count}/{len(output)} new job(s) at accredited employers.")
    print(f"Saved results to {RESULTS_FILE}")
    print(f"Appended {len(new_csv_rows)} new job(s) to {PROCESSED_JOBS_CSV}")


if __name__ == "__main__":
    main()
