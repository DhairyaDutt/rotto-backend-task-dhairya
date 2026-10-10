import os
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

from . import config, store
from .fetch import BudgetExceeded, Fetcher
from .sources import linkedin

REFRESH_DAYS = int(os.environ.get("REFRESH_DAYS", "5"))
LINKEDIN_QUERIES = [("sales", "United Kingdom")]  # widened later


def known_jobs(conn, source):
    rows = conn.execute(
        "SELECT source_job_id, last_detail_checked_at FROM jobs WHERE source = %s",
        (source,),
    ).fetchall()
    return {r["source_job_id"]: r["last_detail_checked_at"] for r in rows}


def crawl_linkedin(fetcher, known, now):
    discovered, details, failed = {}, [], []
    complete = True
    try:
        for keywords, location in LINKEDIN_QUERIES:
            url = ("https://www.linkedin.com/jobs/search/?keywords="
                   f"{quote_plus(keywords)}&location={quote_plus(location)}")
            r = fetcher.get("linkedin", "search", url,
                            validate=lambda t: "base-search-card" in t)
            found = linkedin.discover(r.text) if r.ok else {}
            if not found:
                complete = False
            discovered.update(found)

        refresh_after = timedelta(days=REFRESH_DAYS)
        for job_id, posted_date in discovered.items():
            last_checked = known.get(job_id)
            if last_checked and now - last_checked < refresh_after:
                continue  # unchanged recently: skip the detail request
            url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
            r = fetcher.get("linkedin", "detail", url,
                            validate=lambda t, j=job_id: "show-more-less-html" in t and j in t)
            if r.gone:
                continue  # 404 is evidence, but removal confirmation comes later
            if not r.ok:
                failed.append(job_id)
                continue
            job, state = linkedin.parse_job(r.text, job_id, fetched_at=now,
                                            posted_date=posted_date)
            if state == "ok":
                details.append(job)
            elif state == "bad_page":
                failed.append(job_id)
    except BudgetExceeded as e:
        print("stopped early:", e)
        complete = False

    return {
        "observed_at": now.isoformat(),
        "source": "linkedin",
        "discovery_complete": complete,
        "discovered_source_job_ids": list(discovered),
        "details": details,
        "failed_detail_source_job_ids": failed,
        "confirmed_removed_source_job_ids": [],
    }


def main():
    started = datetime.now(timezone.utc)
    conn = store.connect()
    store.init_schema(conn)
    fetcher = Fetcher()
    run = crawl_linkedin(fetcher, known_jobs(conn, "linkedin"), started)
    counts = store.apply_run(conn, run)
    print("discovered:", len(run["discovered_source_job_ids"]),
          "| details:", len(run["details"]), "| failed:", len(run["failed_detail_source_job_ids"]))
    print("counts:", counts)
    print("requests:", fetcher.log.summary())


if __name__ == "__main__":
    main()