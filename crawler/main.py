import os
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

from . import store
from .fetch import BudgetExceeded, Fetcher
from .sources import linkedin, reed

REFRESH_DAYS = int(os.environ.get("REFRESH_DAYS", "5"))
SOURCES = [s.strip() for s in os.environ.get("SOURCES", "linkedin,reed").split(",") if s.strip()]
LINKEDIN_QUERIES = [("sales", "United Kingdom")]  # widened later
REED_PAGES = int(os.environ.get("REED_PAGES", "2"))


def known_jobs(conn, source):
    rows = conn.execute(
        "SELECT source_job_id, last_detail_checked_at FROM jobs WHERE source = %s",
        (source,),
    ).fetchall()
    return {r["source_job_id"]: r["last_detail_checked_at"] for r in rows}


def make_run(source, now, discovered, details, failed, complete):
    return {
        "observed_at": now.isoformat(),
        "source": source,
        "discovery_complete": complete,
        "discovered_source_job_ids": list(discovered),
        "details": details,
        "failed_detail_source_job_ids": failed,
        "confirmed_removed_source_job_ids": [],
    }


def is_fresh(known, job_id, now):
    last_checked = known.get(job_id)
    return bool(last_checked) and now - last_checked < timedelta(days=REFRESH_DAYS)


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

        for job_id, posted_date in discovered.items():
            if is_fresh(known, job_id, now):
                continue  # checked recently: skip the detail request
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
    return make_run("linkedin", now, discovered, details, failed, complete)


def crawl_reed(fetcher, known, now):
    discovered, details, failed = {}, [], []
    complete = True
    try:
        for page in range(1, REED_PAGES + 1):
            url = f"https://www.reed.co.uk/jobs?pageno={page}"
            r = fetcher.get("reed", "search", url, validate=lambda t: "/jobs/" in t)
            found = reed.discover(r.text) if r.ok else {}
            if not found:
                complete = False
                continue
            discovered.update(found)

        for job_id, path in discovered.items():
            if is_fresh(known, job_id, now):
                continue
            r = fetcher.get("reed", "detail", "https://www.reed.co.uk" + path,
                            validate=lambda t, j=job_id: '"jobDetails"' in t and j in t)
            if r.gone:
                continue
            if not r.ok:
                failed.append(job_id)
                continue
            job, state = reed.parse_job(r.text, job_id)
            if state == "ok":
                details.append(job)
            elif state == "bad_page":
                failed.append(job_id)
    except BudgetExceeded as e:
        print("stopped early:", e)
        complete = False
    return make_run("reed", now, discovered, details, failed, complete)


CRAWLERS = {"linkedin": crawl_linkedin, "reed": crawl_reed}


def main():
    conn = store.connect()
    store.init_schema(conn)
    fetcher = Fetcher()
    for name in SOURCES:
        now = datetime.now(timezone.utc)
        run = CRAWLERS[name](fetcher, known_jobs(conn, name), now)
        counts = store.apply_run(conn, run)
        print(f"[{name}] discovered: {len(run['discovered_source_job_ids'])}"
              f" | details: {len(run['details'])}"
              f" | failed: {len(run['failed_detail_source_job_ids'])}")
        print(f"[{name}] counts: {counts}")
    print("requests:", fetcher.log.summary())


if __name__ == "__main__":
    main()
