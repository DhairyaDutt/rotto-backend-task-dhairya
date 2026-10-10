import os
from datetime import datetime

import psycopg
from psycopg.rows import dict_row

from . import config
from .textutil import content_hash

COLUMNS = [
    "url", "title", "company", "company_logo_url", "location", "work_type",
    "salary_min", "salary_max", "salary_period", "posted_at", "description",
]


def connect(url=None):
    # autocommit: each apply_run opens its own real transaction
    return psycopg.connect(url or config.DATABASE_URL, autocommit=True,
                           row_factory=dict_row)


def init_schema(conn):
    path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(path, encoding="utf-8") as f:
        conn.execute(f.read())


def when(value):
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _num(v):
    return None if v is None else str(float(v))


def job_hash(job):
    """Fingerprint of the content fields. Dates are never included.
    The logo address loses its query string (LinkedIn adds changing tokens)."""
    logo = (job.get("company_logo_url") or "").split("?")[0] or None
    return content_hash(
        job.get("url"), job.get("title"), job.get("company"), logo,
        job.get("location"), job.get("work_type"),
        _num(job.get("salary_min")), _num(job.get("salary_max")),
        job.get("salary_period"), job.get("description"),
    )


def _upsert(conn, job_id, job, at, counts):
    source, source_job_id = job_id.split(":", 1)
    values = {c: job.get(c) for c in COLUMNS}
    values["posted_at"] = when(values["posted_at"])
    new_hash = job_hash(job)

    row = conn.execute(
        "SELECT content_hash FROM jobs WHERE id = %s FOR UPDATE", (job_id,)
    ).fetchone()

    if row is None:
        conn.execute(
            """INSERT INTO jobs (id, source, source_job_id, url, title, company,
                   company_logo_url, location, work_type, salary_min, salary_max,
                   salary_period, posted_at, description, canonical_job_id,
                   content_hash, first_seen_at, last_seen_at, last_detail_checked_at)
               VALUES (%(id)s, %(source)s, %(sjid)s, %(url)s, %(title)s, %(company)s,
                   %(company_logo_url)s, %(location)s, %(work_type)s, %(salary_min)s,
                   %(salary_max)s, %(salary_period)s, %(posted_at)s, %(description)s,
                   %(canonical)s, %(hash)s, %(at)s, %(at)s, %(at)s)""",
            {**values, "id": job_id, "source": source, "sjid": source_job_id,
             "canonical": job.get("canonical_job_id") or job_id,
             "hash": new_hash, "at": at},
        )
        counts["jobs_new"] += 1
        return

    revive = ("removed_at = CASE WHEN removed_at IS NULL OR removed_at <= %(at)s "
              "THEN NULL ELSE removed_at END")
    if row["content_hash"] != new_hash:
        conn.execute(
            f"""UPDATE jobs SET url=%(url)s, title=%(title)s, company=%(company)s,
                   company_logo_url=%(company_logo_url)s, location=%(location)s,
                   work_type=%(work_type)s, salary_min=%(salary_min)s,
                   salary_max=%(salary_max)s, salary_period=%(salary_period)s,
                   posted_at=%(posted_at)s, description=%(description)s,
                   content_hash=%(hash)s, {revive},
                   last_seen_at=GREATEST(last_seen_at, %(at)s),
                   last_detail_checked_at=GREATEST(last_detail_checked_at, %(at)s)
               WHERE id=%(id)s""",
            {**values, "id": job_id, "hash": new_hash, "at": at},
        )
        counts["jobs_changed"] += 1
    else:
        conn.execute(
            f"""UPDATE jobs SET {revive},
                   last_seen_at=GREATEST(last_seen_at, %(at)s),
                   last_detail_checked_at=GREATEST(last_detail_checked_at, %(at)s)
               WHERE id=%(id)s""",
            {"id": job_id, "at": at},
        )


def apply_run(conn, run):
    """Apply one crawl run in a single transaction and return per-run counts.

    Only confirmed_removed_source_job_ids can set removed_at. Absence from
    discovery, failed details and incomplete runs never remove anything.
    """
    at = when(run["observed_at"])
    source = run["source"]
    removed = {f"{source}:{x}" for x in run.get("confirmed_removed_source_job_ids") or []}
    counts = {"jobs_new": 0, "jobs_changed": 0, "jobs_removed": 0}

    with conn.transaction():
        # 1. detail pages fetched successfully: insert new / update changed
        for d in run.get("details") or []:
            job_id = f"{d.get('source', source)}:{d['source_job_id']}"
            if job_id not in removed:
                _upsert(conn, job_id, d, at, counts)

        # 2. listings seen live: advance last_seen_at, clear removed_at.
        #    A failed detail fetch still counts as seen but never creates a row.
        seen = [f"{source}:{x}" for x in run.get("discovered_source_job_ids") or []
                if f"{source}:{x}" not in removed]
        if seen:
            conn.execute(
                """UPDATE jobs SET last_seen_at = GREATEST(last_seen_at, %s),
                       removed_at = CASE WHEN removed_at IS NULL OR removed_at <= %s
                                         THEN NULL ELSE removed_at END
                   WHERE id = ANY(%s)""",
                (at, at, seen),
            )

        # 3. explicit removal evidence
        for job_id in removed:
            cur = conn.execute(
                """UPDATE jobs SET removed_at = %s
                   WHERE id = %s AND removed_at IS NULL AND last_seen_at <= %s""",
                (at, job_id, at),
            )
            counts["jobs_removed"] += cur.rowcount
    return counts