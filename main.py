#!/usr/bin/env python3
"""US Job Hunter — daily job search, AI matching, and email digest.

Usage:
    python main.py              # normal run: fetch, score, send email
    python main.py --dry-run    # fetch and score, but only print results, no email sent
"""

import argparse
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import yaml

import db
import notify
from filters import count_keyword_hits, has_positive_sponsorship_phrase, rule_reject_reason
from scoring import get_client, score_job
from sources import adzuna, ats_boards, usajobs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("job_hunter.main")


def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_all_jobs(config: dict, stats: dict) -> list[dict]:
    keywords = config["search"]["keywords"]
    all_jobs: list[dict] = []
    fetched_by_source = {}

    sources_cfg = config.get("sources", {})

    if sources_cfg.get("adzuna", {}).get("enabled"):
        try:
            jobs = adzuna.fetch_jobs(
                keywords,
                country=sources_cfg["adzuna"].get("country", "us"),
                results_per_keyword=sources_cfg["adzuna"].get("results_per_keyword", 20),
            )
            fetched_by_source["adzuna"] = len(jobs)
            all_jobs.extend(jobs)
        except Exception:
            logger.exception("Adzuna source failed")
            fetched_by_source["adzuna"] = 0

    if sources_cfg.get("usajobs", {}).get("enabled"):
        try:
            jobs = usajobs.fetch_jobs(
                keywords,
                results_per_keyword=sources_cfg["usajobs"].get("results_per_keyword", 20),
            )
            fetched_by_source["usajobs"] = len(jobs)
            all_jobs.extend(jobs)
        except Exception:
            logger.exception("USAJobs source failed")
            fetched_by_source["usajobs"] = 0

    if sources_cfg.get("ats_boards", {}).get("enabled"):
        try:
            jobs = ats_boards.fetch_jobs(
                sources_cfg["ats_boards"].get("companies_file", "companies.yaml"),
                request_delay_seconds=sources_cfg["ats_boards"].get("request_delay_seconds", 1.5),
            )
            fetched_by_source["ats_boards"] = len(jobs)
            all_jobs.extend(jobs)
        except Exception:
            logger.exception("ATS boards source failed")
            fetched_by_source["ats_boards"] = 0

    stats["fetched_by_source"] = fetched_by_source
    stats["fetched_total"] = len(all_jobs)
    return all_jobs


def run(config: dict, dry_run: bool) -> int:
    stats: dict = {}
    eastern_date = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    stats["date"] = eastern_date

    conn = db.connect(config["database"]["path"])

    all_jobs = fetch_all_jobs(config, stats)

    search_cfg = config["search"]
    prefilter_cfg = config["prefilter"]
    visa_cfg = config["visa_filter"]
    scoring_cfg = config["scoring"]

    keywords = search_cfg["keywords"]
    min_hits = prefilter_cfg.get("min_keyword_hits", 1)

    # 1) keyword pre-filter (cheap, runs before any DB/AI work)
    candidates = []
    for job in all_jobs:
        hits = count_keyword_hits(job.get("title", ""), job.get("description", ""), keywords)
        if hits >= min_hits:
            job["_keyword_hits"] = hits
            candidates.append(job)
    stats["prefilter_passed"] = len(candidates)

    # 2) de-dup against jobs already seen in previous runs
    new_candidates = []
    for job in candidates:
        uid = db.compute_uid(
            job["source"], job.get("source_job_id"), job.get("company", ""), job.get("title", ""), job.get("location", "")
        )
        job["_uid"] = uid
        if not db.is_seen(conn, uid):
            new_candidates.append(job)
    stats["new_after_dedup"] = len(new_candidates)

    # 3) rule-based visa/sponsorship rejection
    reject_phrases = visa_cfg.get("reject_phrases", [])
    positive_phrases = visa_cfg.get("positive_phrases", [])
    survivors = []
    rule_rejected_count = 0
    for job in new_candidates:
        reason = rule_reject_reason(job.get("description", ""), job.get("title", ""), reject_phrases)
        if reason:
            if not dry_run:
                db.record_job(conn, job["_uid"], job, rejected_reason=f"rule:{reason}")
            rule_rejected_count += 1
            continue
        job["_positive_sponsorship_hint"] = has_positive_sponsorship_phrase(
            job.get("description", ""), job.get("title", ""), positive_phrases
        )
        survivors.append(job)
    stats["rule_rejected"] = rule_rejected_count

    # 4) cap how many go to the (paid) AI scoring step, prioritizing stronger keyword matches
    survivors.sort(key=lambda j: (j["_positive_sponsorship_hint"], j["_keyword_hits"]), reverse=True)
    max_to_ai = prefilter_cfg.get("max_jobs_to_ai_per_run", 80)
    to_score = survivors[:max_to_ai]
    stats["sent_to_ai"] = len(to_score)

    # 5) AI scoring
    notified_jobs = []
    score_threshold = scoring_cfg.get("score_threshold", 60)
    reject_sponsorship = set(scoring_cfg.get("reject_sponsorship_likelihood", ["unlikely"]))

    if to_score:
        client = get_client()
        for job in to_score:
            result = score_job(
                client,
                model=scoring_cfg["model"],
                max_tokens=scoring_cfg.get("max_tokens", 300),
                candidate_profile=config["candidate_profile"],
                job=job,
            )
            if result is None:
                continue
            passed = result["score"] >= score_threshold and result["sponsorship_likelihood"] not in reject_sponsorship
            if not dry_run:
                db.record_job(
                    conn,
                    job["_uid"],
                    job,
                    score=result["score"],
                    sponsorship_likelihood=result["sponsorship_likelihood"],
                    seniority_fit=result["seniority_fit"],
                    reason=result["one_line_reason"],
                    notified=passed,
                )
            if passed:
                notified_jobs.append({**job, **result})

    stats["final_notified"] = len(notified_jobs)

    if dry_run:
        print(f"\n=== US Job Hunter dry-run — {eastern_date} ===")
        print(f"Stats: {stats}\n")
        for job in sorted(notified_jobs, key=lambda j: j["score"], reverse=True):
            print(f"[{job['score']:3d}] {job['title']} @ {job['company']} ({job['location']})")
            print(f"      sponsorship={job['sponsorship_likelihood']} seniority={job['seniority_fit']}")
            print(f"      {job['one_line_reason']}")
            print(f"      {job['url']}\n")
        if not notified_jobs:
            print("(no new matching jobs today)")
        return 0

    display_stats = {"抓取总数": stats["fetched_total"]}
    for source_name, count in stats["fetched_by_source"].items():
        display_stats[f"  - {source_name} 抓取数"] = count
    display_stats.update({
        "关键词粗筛通过": stats["prefilter_passed"],
        "去重后新职位": stats["new_after_dedup"],
        "规则剔除(签证)": stats["rule_rejected"],
        "送 AI 打分": stats["sent_to_ai"],
        "最终推送": stats["final_notified"],
    })
    ok = notify.send_daily_email(
        config["email"]["provider"],
        config["email"].get("subject_prefix", "[US Job Hunter]"),
        eastern_date,
        notified_jobs,
        display_stats,
    )
    if not ok:
        logger.error("Failed to send daily email")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="US Job Hunter daily search")
    parser.add_argument("--dry-run", action="store_true", help="print results instead of sending email")
    parser.add_argument("--config", default="config.yaml", help="path to config.yaml")
    args = parser.parse_args()

    config = load_config(args.config)
    return run(config, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
