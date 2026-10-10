import os
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

from . import config, dedupe, export, store
from .fetch import BudgetExceeded, Fetcher
from .sources import indeed, linkedin, reed

REFRESH_DAYS = int(os.environ.get("REFRESH_DAYS", "5"))
SOURCES = [s.strip() for s in os.environ.get("SOURCES", "linkedin,reed,indeed").split(",") if s.strip()]
STALE_DAYS = int(os.environ.get("STALE_DAYS", "3"))   # unseen this long -> verify
MAX_VERIFY = int(os.environ.get("MAX_VERIFY", "20"))  # verification requests per source
INDEED_PAGES = int(os.environ.get("INDEED_PAGES", "1"))
KEYWORDS = ["sales", "engineer", "nurse", "driver", "accountant", "teacher",
            "developer", "warehouse", "manager", "customer service",
            "electrician", "chef"]
LOCATIONS = ["United Kingdom", "London", "Manchester", "Birmingham", "Leeds",
             "Glasgow", "Bristol", "Cardiff"]
# keyword x location slices, interleaved so a small request budget still
# covers many different searches instead of one deep one
_pairs = [(KEYWORDS[i % len(KEYWORDS)], LOCATIONS[(i * 3 + i // len(KEYWORDS)) % len(LOCATIONS)])
          for i in range(len(KEYWORDS) * len(LOCATIONS))]
QUERIES = list(dict.fromkeys(_pairs))
MAX_SEARCHES = int(os.environ.get("MAX_SEARCHES", "12"))  # search requests per source
LINKEDIN_QUERIES = QUERIES
REED_PAGES = int(os.environ.get("REED_PAGES", "1"))
REED_QUERIES = QUERIES
INDEED_QUERIES = QUERIES


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
        for keywords, location in LINKEDIN_QUERIES[:MAX_SEARCHES]:
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
        for keywords, location in REED_QUERIES[:MAX_SEARCHES]:
            for page in range(1, REED_PAGES + 1):
                url = ("https://www.reed.co.uk/jobs?keywords="
                       f"{quote_plus(keywords)}&location={quote_plus(location)}&pageno={page}")
                r = fetcher.get("reed", "search", url, validate=lambda t: "/jobs/" in t)
                found = reed.discover(r.text) if r.ok else {}
                if not found:
                    complete = False
                    break
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


def crawl_indeed(fetcher, known, now):
    discovered, details, failed = {}, [], []
    page_of = {}   # jobkey -> search page it was found on (needed for detail)
    complete = True
    try:
        for keywords, location in INDEED_QUERIES[:MAX_SEARCHES]:
            url = ("https://uk.indeed.com/jobs?q="
                   f"{quote_plus(keywords)}&l={quote_plus(location)}")
            for _ in range(INDEED_PAGES):
                r = fetcher.get("indeed", "search", url,
                                validate=lambda t: "mosaic-provider-jobcards" in t)
                found = indeed.discover(r.text) if r.ok else {}
                if not found:
                    complete = False
                    break
                for jk, card in found.items():
                    discovered.setdefault(jk, card)
                    page_of.setdefault(jk, url)
                nxt = indeed.next_page(r.text)
                if not nxt:
                    break
                url = "https://uk.indeed.com" + nxt

        for jk, card in discovered.items():
            if is_fresh(known, jk, now):
                continue
            r = fetcher.get("indeed", "detail", f"{page_of[jk]}&vjk={jk}",
                            validate=lambda t, j=jk: f'"jobKey":"{j}"' in t)
            if not r.ok:
                failed.append(jk)   # no removal evidence for Indeed: never remove
                continue
            job, state = indeed.parse_job(r.text, jk, card=card)
            if state == "ok":
                details.append(job)
            elif state == "bad_page":
                failed.append(jk)
    except BudgetExceeded as e:
        print("stopped early:", e)
        complete = False
    return make_run("indeed", now, list(discovered), details, failed, complete)


def verify_stale(fetcher, conn, name, run, now):
    """Jobs not seen this run for STALE_DAYS: ask the job page itself.
    Removal needs the board to say 'gone' twice (or an explicit expired page)."""
    seen = set(run["discovered_source_job_ids"])
    rows = conn.execute(
        """SELECT source_job_id, url FROM jobs
           WHERE source = %s AND removed_at IS NULL AND last_seen_at < %s
           ORDER BY last_seen_at LIMIT %s""",
        (name, now - timedelta(days=STALE_DAYS), MAX_VERIFY)).fetchall()
    for row in rows:
        job_id = row["source_job_id"]
        if job_id in seen:
            continue
        if name == "linkedin":
            url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
        else:
            url = row["url"]
        try:
            r = fetcher.get(name, "verify", url)
            gone = r.gone
            if gone:   # second request to be sure it is not a glitch
                gone = fetcher.get(name, "verify", url).gone
            elif r.ok and name == "reed":
                _, state = reed.parse_job(r.text, job_id)
                gone = state == "expired"
        except BudgetExceeded:
            break
        if gone:
            run["confirmed_removed_source_job_ids"].append(job_id)


def refresh_pending(fetcher, conn, run, now):
    """Known Reed jobs flagged for re-check (last_detail_checked_at is NULL) are
    re-fetched even if this run's searches did not show them."""
    have = {d["source_job_id"] for d in run["details"]}
    rows = conn.execute(
        """SELECT source_job_id, url FROM jobs
           WHERE source = 'reed' AND removed_at IS NULL
             AND last_detail_checked_at IS NULL LIMIT %s""", (MAX_VERIFY * 3,)).fetchall()
    for row in rows:
        job_id = row["source_job_id"]
        if job_id in have:
            continue
        try:
            r = fetcher.get("reed", "detail", row["url"],
                            validate=lambda t, j=job_id: '"jobDetails"' in t and j in t)
        except BudgetExceeded:
            break
        if r.ok:
            job, state = reed.parse_job(r.text, job_id)
            if state == "ok":
                run["details"].append(job)


CRAWLERS = {"linkedin": crawl_linkedin, "reed": crawl_reed, "indeed": crawl_indeed}


def main():
    started_at = datetime.now(timezone.utc)
    conn = store.connect()
    store.init_schema(conn)
    fetcher = Fetcher(source_limit=max(1, config.MAX_REQUESTS // len(SOURCES)))
    totals = {"jobs_new": 0, "jobs_changed": 0, "jobs_removed": 0}
    for name in SOURCES:
        now = datetime.now(timezone.utc)
        try:
            run = CRAWLERS[name](fetcher, known_jobs(conn, name), now)
            if name == "reed":
                refresh_pending(fetcher, conn, run, now)
            if name in ("linkedin", "reed"):
                verify_stale(fetcher, conn, name, run, now)
        except Exception as e:   # one broken source must not stop the others
            print(f"[{name}] failed: {e!r}")
            continue
        counts = store.apply_run(conn, run)
        for k in totals:
            totals[k] += counts[k]
        print(f"[{name}] discovered: {len(run['discovered_source_job_ids'])}"
              f" | details: {len(run['details'])}"
              f" | failed: {len(run['failed_detail_source_job_ids'])}"
              f" | removed: {counts['jobs_removed']}")
    linked = dedupe.run(conn)
    print("duplicate rows linked:", linked)
    export.write_outputs(conn, started_at, datetime.now(timezone.utc),
                         fetcher.log, totals)
    print("requests:", fetcher.log.summary())


if __name__ == "__main__":
    main()
