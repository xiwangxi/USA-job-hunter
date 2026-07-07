"""Adapter for public Greenhouse / Lever / Ashby job board JSON endpoints.

Companies to poll are configured in companies.yaml (editable, no code
changes needed to add/remove a company).
"""

import logging
import time

import yaml

from sources.common import fetch_json, is_us_location, strip_html

logger = logging.getLogger("job_hunter.sources.ats_boards")

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
LEVER_URL = "https://api.lever.co/v0/postings/{token}"
ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{token}"


def load_companies(companies_file: str) -> list[dict]:
    try:
        with open(companies_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning("companies file not found: %s", companies_file)
        return []
    return data.get("companies", []) or []


def _fetch_greenhouse(company_name: str, token: str) -> list[dict]:
    data = fetch_json(GREENHOUSE_URL.format(token=token), params={"content": "true"})
    if not data:
        return []
    jobs = []
    for item in data.get("jobs", []):
        location = (item.get("location", {}) or {}).get("name", "")
        if not is_us_location(location):
            continue
        jobs.append({
            "source": f"greenhouse:{token}",
            "source_job_id": str(item.get("id", "")),
            "title": (item.get("title") or "").strip(),
            "company": company_name,
            "location": location or "United States",
            "description": strip_html(item.get("content", "")),
            "url": item.get("absolute_url", ""),
        })
    return jobs


def _fetch_lever(company_name: str, token: str) -> list[dict]:
    data = fetch_json(LEVER_URL.format(token=token), params={"mode": "json"})
    if not data or not isinstance(data, list):
        return []
    jobs = []
    for item in data:
        location = (item.get("categories", {}) or {}).get("location", "")
        if not is_us_location(location):
            continue
        description = item.get("descriptionPlain") or strip_html(item.get("description", ""))
        lists = item.get("lists", []) or []
        extra = " ".join(strip_html(section.get("content", "")) for section in lists)
        jobs.append({
            "source": f"lever:{token}",
            "source_job_id": str(item.get("id", "")),
            "title": (item.get("text") or "").strip(),
            "company": company_name,
            "location": location or "United States",
            "description": f"{description} {extra}".strip(),
            "url": item.get("hostedUrl", ""),
        })
    return jobs


def _fetch_ashby(company_name: str, token: str) -> list[dict]:
    data = fetch_json(ASHBY_URL.format(token=token))
    if not data:
        return []
    jobs = []
    for item in data.get("jobs", []):
        location = item.get("location", "") or item.get("locationName", "")
        if not is_us_location(location):
            continue
        description = item.get("descriptionPlain") or strip_html(item.get("descriptionHtml", ""))
        jobs.append({
            "source": f"ashby:{token}",
            "source_job_id": str(item.get("id", "")),
            "title": (item.get("title") or "").strip(),
            "company": company_name,
            "location": location or "United States",
            "description": description,
            "url": item.get("jobUrl", "") or item.get("applyUrl", ""),
        })
    return jobs


_FETCHERS = {
    "greenhouse": _fetch_greenhouse,
    "lever": _fetch_lever,
    "ashby": _fetch_ashby,
}


def fetch_jobs(companies_file: str, *, request_delay_seconds: float = 1.5) -> list[dict]:
    companies = load_companies(companies_file)
    jobs: list[dict] = []
    for entry in companies:
        ats = (entry.get("ats") or "none").lower()
        token = entry.get("token")
        name = entry.get("name", token or "unknown")
        fetcher = _FETCHERS.get(ats)
        if not fetcher or not token:
            continue
        try:
            company_jobs = fetcher(name, token)
            jobs.extend(company_jobs)
            logger.info("%s (%s:%s): %d jobs", name, ats, token, len(company_jobs))
        except Exception:
            logger.exception("Failed to fetch jobs for %s (%s:%s)", name, ats, token)
        time.sleep(request_delay_seconds)
    logger.info("ATS boards: fetched %d jobs across %d companies", len(jobs), len(companies))
    return jobs
