"""Writes output/jobs.jsonl and output/report.json from the database."""
import json
import os
from datetime import datetime
from decimal import Decimal

COLUMNS = [
    "id", "source", "source_job_id", "url", "title", "company",
    "company_logo_url", "location", "work_type", "salary_min", "salary_max",
    "salary_period", "posted_at", "description", "canonical_job_id",
    "content_hash", "first_seen_at", "last_seen_at", "removed_at",
]

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "output")
# Proxy traffic is billed on compressed bytes, but Python only sees unpacked
# bytes. These ratios were measured during recon (compressed / unpacked).
WIRE_RATIO = {"reed": 0.40, "linkedin": 0.12, "indeed": 0.18}
DEFAULT_RATIO = 0.25
USD_PER_GB = float(os.environ.get("PROXY_USD_PER_GB", "5.0"))  # assumption


def plain(value):
    """Make a database value JSON-friendly (no loss of meaning)."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def _write_atomic(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)  # a crash never leaves a half-written file


def write_jobs(conn, path):
    """One job per line, every stored row (removed ones included)."""
    lines = []
    cur = conn.execute(f"SELECT {', '.join(COLUMNS)} FROM jobs ORDER BY id")
    for row in cur:
        lines.append(json.dumps({c: plain(row[c]) for c in COLUMNS}, ensure_ascii=False))
    _write_atomic(path, "".join(line + "\n" for line in lines))
    return len(lines)


def estimated_cost_usd(bytes_by_source):
    wire = sum(b * WIRE_RATIO.get(src, DEFAULT_RATIO) for src, b in bytes_by_source.items())
    return round(wire / 1e9 * USD_PER_GB, 4), int(wire)


def build_report(conn, started_at, finished_at, log, totals):
    summary = log.summary()
    live = {r["source"]: r["n"] for r in conn.execute(
        "SELECT source, count(*) AS n FROM jobs WHERE removed_at IS NULL GROUP BY source")}
    linked = conn.execute(
        "SELECT count(*) AS n FROM jobs WHERE canonical_job_id IS NOT NULL "
        "AND canonical_job_id <> id").fetchone()["n"]
    distinct = conn.execute(
        "SELECT count(DISTINCT canonical_job_id) AS n FROM jobs "
        "WHERE removed_at IS NULL").fetchone()["n"]
    cost, wire_bytes = estimated_cost_usd(log.bytes_by_source())
    return {
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "requests_total": summary["requests_total"],
        "requests_blocked": summary["requests_blocked"],
        "requests_by_status": summary["requests_by_status"],
        "jobs_by_source": live,
        "duplicates_linked": linked,
        "jobs_new": totals["jobs_new"],
        "jobs_removed": totals["jobs_removed"],
        "jobs_changed": totals["jobs_changed"],
        "estimated_cost_usd": cost,
        # extra context, not required by the brief
        "requests_by_source": log.requests_by_source(),
        "bytes_unpacked": summary["bytes_total"],
        "bytes_proxy_estimate": wire_bytes,
        "distinct_canonical_jobs": distinct,
        "assumptions": {
            "usd_per_gb": USD_PER_GB,
            "compressed_over_unpacked": WIRE_RATIO,
        },
    }


def write_outputs(conn, started_at, finished_at, log, totals, out_dir=None):
    out_dir = out_dir or OUTPUT_DIR
    n = write_jobs(conn, os.path.join(out_dir, "jobs.jsonl"))
    report = build_report(conn, started_at, finished_at, log, totals)
    _write_atomic(os.path.join(out_dir, "report.json"),
                  json.dumps(report, indent=2) + "\n")
    return n, report
