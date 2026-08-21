"""
Cross-check SEEK job listings (seek_jobs.jsonl) against the NZ Immigration
Accredited Employer list, using accredited_employer_api.check_employer_accredited().

If a company name contains "Ltd" and isn't found, retries once with "Ltd"
replaced by "Limited" (the immigration site's search wants the full form).
"""

import json
import os
import re
import time

from accredited_employer_api import check_employer_accredited

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
JOBS_FILE = os.path.join(SCRIPT_DIR, "seek_jobs.jsonl")
RESULTS_FILE = os.path.join(SCRIPT_DIR, "seek_accreditation_check.json")

LTD_PATTERN = re.compile(r"\bLtd\.?\b", re.IGNORECASE)


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
        print(f"No jobs found in {JOBS_FILE}. Run check_seek_emails.py first.")
        return

    cache = {}
    output = []

    for job in jobs:
        company = job.get("company", "").strip()
        if not company:
            continue

        is_new_company = company not in cache
        result = check_company(company, cache)
        output.append({**job, **result})

        status = "ACCREDITED" if result["accredited"] else "not found"
        detail = f' (as "{result["matched_employer_name"]}")' if result["accredited"] else ""
        print(f"- {job.get('job_title', '?')} @ {company}: {status}{detail}")

        if is_new_company:
            time.sleep(0.5)

    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    accredited_count = sum(1 for r in output if r["accredited"])
    print(f"\n{accredited_count}/{len(output)} job(s) at accredited employers.")
    print(f"Saved results to {RESULTS_FILE}")


if __name__ == "__main__":
    main()
