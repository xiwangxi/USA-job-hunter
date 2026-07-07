"""Shared helpers for source adapters."""

import logging
import re
import time

import requests

_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(html: str | None) -> str:
    if not html:
        return ""
    text = _TAG_RE.sub(" ", html)
    return re.sub(r"\s+", " ", text).strip()

logger = logging.getLogger("job_hunter.sources")

US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming", "district of columbia",
}
US_STATE_ABBRS = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id",
    "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms",
    "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok",
    "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv",
    "wi", "wy", "dc",
}
NON_US_HINTS = {
    "canada", "mexico", "united kingdom", "germany", "france", "india",
    "china", "japan", "singapore", "poland", "ireland", "spain", "italy",
    "netherlands", "switzerland", "australia", "brazil", "remote - emea",
    "remote - apac", "remote, europe", "remote (europe)",
}


def is_us_location(location: str | None) -> bool:
    """Best-effort heuristic: does this location string look like a US (or
    unrestricted remote) posting? Deliberately lenient — the AI scoring
    step reads the full description and will down-rank anything that turns
    out not to actually be US-based/sponsorable.
    """
    if not location:
        return True  # unknown location: let it through, AI will judge
    loc = location.lower()
    for hint in NON_US_HINTS:
        if hint in loc:
            return False
    if "united states" in loc or "usa" in loc or "u.s." in loc:
        return True
    if "remote" in loc:
        return True
    for state in US_STATE_NAMES:
        if state in loc:
            return True
    # location strings like "Austin, TX" -> check trailing 2-letter abbr
    tail = loc.replace(".", "").split(",")[-1].strip()
    if tail in US_STATE_ABBRS:
        return True
    return False


def fetch_json(url, *, params=None, headers=None, timeout=20, retries=2, backoff=1.5):
    """GET a URL and return parsed JSON, or None on failure (logged, never raises)."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            logger.warning("GET %s -> HTTP %s", url, resp.status_code)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(backoff ** (attempt + 1))
                continue
            return None
        except requests.RequestException as exc:
            last_err = exc
            if attempt < retries:
                time.sleep(backoff ** (attempt + 1))
                continue
    if last_err:
        logger.warning("GET %s failed: %s", url, last_err)
    return None
