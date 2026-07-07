"""Historical H-1B/E-3 sponsorship signal from DOL OFLC LCA disclosure data.

Grounds the AI's sponsorship_likelihood judgment in real government filing
data instead of guessing from job-description wording alone: looks up how
many LCA cases (H-1B / H-1B1 / E-3) an employer has filed and how many were
certified.

Data source: DOL Office of Foreign Labor Certification quarterly LCA
disclosure files (public, no API key needed). The exact download link
changes every quarter, so this module either uses an explicit
`resource_url` from config (recommended — see README) or tries to discover
the latest file via the data.gov CKAN API. Both paths are best-effort: any
failure just disables sponsorship enrichment for that run, it never breaks
the rest of the pipeline.
"""

import io
import logging
import re
import sqlite3
from datetime import datetime, timezone

import requests

logger = logging.getLogger("job_hunter.sponsorship_data")

try:
    import openpyxl
except ImportError:  # pragma: no cover
    openpyxl = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS employer_sponsorship (
    employer_key TEXT PRIMARY KEY,
    employer_name TEXT,
    case_count INTEGER,
    certified_count INTEGER,
    source_period TEXT
);
CREATE TABLE IF NOT EXISTS sponsorship_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

CKAN_PACKAGE_SHOW_URL = "https://catalog.data.gov/api/3/action/package_show"

EMPLOYER_NAME_CANDIDATES = ["EMPLOYER_NAME", "EMPLOYER NAME", "PETITIONER_NAME", "EMPLOYER_NAME (PETITIONER)"]
CASE_STATUS_CANDIDATES = ["CASE_STATUS", "STATUS"]

_SUFFIX_RE = re.compile(r"\b(INC|INCORPORATED|LLC|LLP|LTD|LIMITED|CORP|CORPORATION|CO|COMPANY|LP|PLC|GROUP|HOLDINGS)\b\.?")
_NONALNUM_RE = re.compile(r"[^A-Z0-9 ]")
_PERIOD_RE = re.compile(r"FY\s?(20\d{2}).{0,5}Q([1-4])", re.IGNORECASE)


def normalize_employer_name(name: str) -> str:
    if not name:
        return ""
    text = _NONALNUM_RE.sub(" ", name.upper())
    text = _SUFFIX_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def _get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM sponsorship_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO sponsorship_meta (key, value) VALUES (?, ?)", (key, value))
    conn.commit()


def is_stale(conn: sqlite3.Connection, max_age_days: int) -> bool:
    last = _get_meta(conn, "last_refreshed_at")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return True
    return (datetime.now(timezone.utc) - last_dt).days >= max_age_days


def _discover_resource_url(dataset_id: str) -> str | None:
    try:
        resp = requests.get(CKAN_PACKAGE_SHOW_URL, params={"id": dataset_id}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException:
        logger.exception("Failed to query data.gov CKAN API for dataset %s", dataset_id)
        return None
    except ValueError:
        logger.exception("data.gov CKAN API returned non-JSON response for dataset %s", dataset_id)
        return None

    resources = (data.get("result") or {}).get("resources", [])
    candidates = []
    for res in resources:
        name = res.get("name", "") or ""
        fmt = (res.get("format", "") or "").lower()
        if fmt not in ("xlsx", "csv"):
            continue
        if "h-1b" not in name.lower() and "h1b" not in name.lower():
            continue
        match = _PERIOD_RE.search(name)
        sort_key = (int(match.group(1)), int(match.group(2))) if match else (0, 0)
        candidates.append((sort_key, res.get("url"), name))

    if not candidates:
        logger.warning("Could not find an H-1B disclosure resource in data.gov dataset %s", dataset_id)
        return None

    candidates.sort(key=lambda c: c[0], reverse=True)
    best_key, best_url, best_name = candidates[0]
    logger.info("Selected H-1B disclosure resource via data.gov: %s", best_name)
    return best_url


def _resolve_column(header_row: list, candidates: list[str]) -> int | None:
    normalized = [str(h or "").strip().upper().replace(" ", "_") for h in header_row]
    for cand in candidates:
        cand_norm = cand.strip().upper().replace(" ", "_")
        if cand_norm in normalized:
            return normalized.index(cand_norm)
    return None


def _download_and_aggregate(url: str) -> dict[str, dict] | None:
    if openpyxl is None:
        logger.error("openpyxl is not installed; cannot parse LCA disclosure xlsx file")
        return None
    try:
        resp = requests.get(url, timeout=180)
        resp.raise_for_status()
        content = resp.content
    except requests.RequestException:
        logger.exception("Failed to download LCA disclosure file from %s", url)
        return None

    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.active
        rows = ws.iter_rows(values_only=True)
        header = list(next(rows))
    except Exception:
        logger.exception("Failed to open/parse LCA disclosure workbook from %s", url)
        return None

    name_idx = _resolve_column(header, EMPLOYER_NAME_CANDIDATES)
    status_idx = _resolve_column(header, CASE_STATUS_CANDIDATES)
    if name_idx is None:
        logger.error("Could not locate an employer-name column in LCA disclosure file; header was: %s", header)
        return None

    stats: dict[str, dict] = {}
    for row in rows:
        if name_idx >= len(row):
            continue
        employer_name = row[name_idx]
        if not employer_name:
            continue
        key = normalize_employer_name(str(employer_name))
        if not key:
            continue
        entry = stats.setdefault(key, {"employer_name": str(employer_name).strip(), "case_count": 0, "certified_count": 0})
        entry["case_count"] += 1
        if status_idx is not None and status_idx < len(row):
            status = str(row[status_idx] or "").strip().lower()
            if status.startswith("certified"):
                entry["certified_count"] += 1
    return stats


def refresh_if_stale(conn: sqlite3.Connection, *, dataset_id: str, resource_url: str = "", max_age_days: int = 30) -> bool:
    """Best-effort refresh of the employer sponsorship lookup table. Never raises."""
    _ensure_schema(conn)
    if not is_stale(conn, max_age_days):
        return False

    url = resource_url or _discover_resource_url(dataset_id)
    if not url:
        logger.warning("No LCA disclosure resource URL available; skipping sponsorship-data refresh")
        return False

    stats = _download_and_aggregate(url)
    if stats is None:
        return False

    conn.execute("DELETE FROM employer_sponsorship")
    conn.executemany(
        "INSERT INTO employer_sponsorship (employer_key, employer_name, case_count, certified_count, source_period) "
        "VALUES (?, ?, ?, ?, ?)",
        [(key, v["employer_name"], v["case_count"], v["certified_count"], url) for key, v in stats.items()],
    )
    _set_meta(conn, "last_refreshed_at", datetime.now(timezone.utc).isoformat())
    conn.commit()
    logger.info("Refreshed employer sponsorship table with %d employers from %s", len(stats), url)
    return True


def lookup(conn: sqlite3.Connection, company_name: str, legal_name: str | None = None) -> dict | None:
    key = normalize_employer_name(legal_name or company_name)
    if not key:
        return None
    row = conn.execute(
        "SELECT employer_name, case_count, certified_count FROM employer_sponsorship WHERE employer_key = ?",
        (key,),
    ).fetchone()
    if not row:
        return None
    employer_name, case_count, certified_count = row
    return {"employer_name": employer_name, "case_count": case_count, "certified_count": certified_count}
