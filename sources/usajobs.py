"""USAJobs API adapter. https://developer.usajobs.gov/

Note: most federal positions require US citizenship, so this source will
usually be almost entirely filtered out by the visa rule-filter downstream.
It's included per spec as an optional/low-priority source.
"""

import logging
import os

from sources.common import fetch_json, is_us_location

logger = logging.getLogger("job_hunter.sources.usajobs")

BASE_URL = "https://data.usajobs.gov/api/search"


def fetch_jobs(keywords: list[str], *, results_per_keyword: int = 20) -> list[dict]:
    api_key = os.environ.get("USAJOBS_API_KEY")
    user_agent_email = os.environ.get("USAJOBS_USER_AGENT_EMAIL")
    if not api_key or not user_agent_email:
        logger.warning("USAJOBS_API_KEY / USAJOBS_USER_AGENT_EMAIL not set, skipping USAJobs")
        return []

    headers = {
        "Host": "data.usajobs.gov",
        "User-Agent": user_agent_email,
        "Authorization-Key": api_key,
    }

    jobs: list[dict] = []
    seen_ids: set[str] = set()

    for keyword in keywords:
        params = {"Keyword": keyword, "ResultsPerPage": results_per_keyword}
        data = fetch_json(BASE_URL, params=params, headers=headers)
        if not data:
            continue
        items = (data.get("SearchResult", {}) or {}).get("SearchResultItems", [])
        for item in items:
            descriptor = item.get("MatchedObjectDescriptor", {}) or {}
            job_id = str(descriptor.get("PositionID", "")) or str(item.get("MatchedObjectId", ""))
            if not job_id or job_id in seen_ids:
                continue
            location = ""
            locations = descriptor.get("PositionLocation", [])
            if locations:
                location = locations[0].get("LocationName", "")
            if not location:
                location = descriptor.get("PositionLocationDisplay", "")
            if not is_us_location(location):
                continue
            summary = ((descriptor.get("UserArea", {}) or {}).get("Details", {}) or {})
            description = " ".join(filter(None, [
                summary.get("JobSummary", ""),
                descriptor.get("QualificationSummary", ""),
            ]))
            seen_ids.add(job_id)
            jobs.append({
                "source": "usajobs",
                "source_job_id": job_id,
                "title": descriptor.get("PositionTitle", "").strip(),
                "company": descriptor.get("OrganizationName", "US Federal Government"),
                "location": location or "United States",
                "description": description,
                "url": descriptor.get("PositionURI", ""),
            })
    logger.info("USAJobs: fetched %d unique jobs across %d keywords", len(jobs), len(keywords))
    return jobs
