"""One-off: fix Indeed rows saved before the salary rule changed
(-1 meant 'no maximum', 0 meant 'no minimum'). Recomputes content_hash too,
so the next crawl does not report them as changed.   python -m tools.repair_indeed_salary"""
from crawler import store


def normalise(lo, hi, period):
    lo = lo if lo is not None and lo > 0 else None
    hi = hi if hi is not None and hi > 0 else None
    if lo is None and hi is None:
        return None, None, None
    if lo is not None and hi is not None and hi < lo:
        hi = lo
    if hi is None:
        hi = lo
    return lo, hi, period


def main():
    conn = store.connect()
    rows = conn.execute(
        """SELECT * FROM jobs WHERE source = 'indeed' AND salary_period IS NOT NULL
           AND (salary_min <= 0 OR salary_max <= 0 OR salary_min > salary_max)""").fetchall()
    for r in rows:
        lo, hi, period = normalise(r["salary_min"], r["salary_max"], r["salary_period"])
        new = dict(r, salary_min=lo, salary_max=hi, salary_period=period)
        conn.execute(
            "UPDATE jobs SET salary_min=%s, salary_max=%s, salary_period=%s, content_hash=%s WHERE id=%s",
            (lo, hi, period, store.job_hash(new), r["id"]))
    print(f"repaired {len(rows)} Indeed rows")


if __name__ == "__main__":
    main()
