"""Adzuna API adapter. https://developer.adzuna.com/"""

import logging
import os

from sources.common import fetch_json, is_us_location

logger = logging.getLogger("job_hunter.sources.adzuna")

BASE_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/1"


def fetch_jobs(keywords: list[str], *, country: str = "us", results_per_keyword: int = 20) -> list[dict]:
    app_id = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        logger.warning("ADZUNA_APP_ID / ADZUNA_APP_KEY not set, skipping Adzuna")
        return []

    jobs: list[dict] = []
    seen_ids: set[str] = set()
    url = BASE_URL.format(country=country)

    for keyword in keywords:
        params = {
            "app_id": app_id,
            "app_key": app_key,
            "results_per_page": results_per_keyword,
            "what": keyword,
            "content-type": "application/json",
        }
        data = fetch_json(url, params=params)
        if not data:
            continue
        for item in data.get("results", []):
            job_id = str(item.get("id", ""))
            if not job_id or job_id in seen_ids:
                continue
            location = (item.get("location", {}) or {}).get("display_name", "")
            if not is_us_location(location):
                continue
            seen_ids.add(job_id)
            jobs.append({
                "source": "adzuna",
                "source_job_id": job_id,
                "title": item.get("title", "").strip(),
                "company": (item.get("company", {}) or {}).get("display_name", "Unknown"),
                "location": location or "United States",
                "description": item.get("description", "") or "",
                "url": item.get("redirect_url", ""),
            })
    logger.info("Adzuna: fetched %d unique jobs across %d keywords", len(jobs), len(keywords))
    return jobs
