"""Offline audit of output/jobs.jsonl against the brief's validity rules.
No proxy, no network: python tools/audit.py [path]"""
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

PATH = sys.argv[1] if len(sys.argv) > 1 else "output/jobs.jsonl"
FIELDS = ["id", "source", "source_job_id", "url", "title", "company", "company_logo_url",
          "location", "work_type", "salary_min", "salary_max", "salary_period", "posted_at",
          "description", "canonical_job_id", "content_hash", "first_seen_at", "last_seen_at",
          "removed_at"]
WORK = {"full time", "part time", "contract", "temporary", None}
PERIODS = {"year", "day", "hour", None}
RANGE = {"year": (5000, 1_000_000), "day": (20, 5000), "hour": (4, 500)}
HOSTS = {"reed": "reed.co.uk", "linkedin": "linkedin.com", "indeed": "indeed.com"}
HTML = re.compile(r"</?[a-zA-Z][^>]*>|&nbsp;|&amp;|&lt;|&gt;|&#\d+;")


def ts(v):
    return datetime.fromisoformat(v.replace("Z", "+00:00")) if v else None


rows = [json.loads(line) for line in open(PATH, encoding="utf-8")]
now = datetime.now(timezone.utc)
bad = defaultdict(lambda: defaultdict(list))   # source -> rule -> [ids]
warn = defaultdict(lambda: defaultdict(list))
by_id = {r["id"]: r for r in rows}


def fail(r, rule):
    bad[r["source"]][rule].append(r["id"])


def note(r, rule):
    warn[r["source"]][rule].append(r["id"])


for r in rows:
    if [k for k in FIELDS if k not in r]:
        fail(r, "missing schema field")
    if r["id"] != f"{r['source']}:{r['source_job_id']}":
        fail(r, "id != source:source_job_id")
    url = r.get("url") or ""
    if HOSTS.get(r["source"], "?") not in url:
        fail(r, "url host does not match source")
    if str(r["source_job_id"]) not in url:
        fail(r, "url does not contain job id")
    for k in ("title", "location", "description"):
        if not (r.get(k) or "").strip():
            fail(r, f"empty {k}")
    d = r.get("description") or ""
    if HTML.search(d):
        fail(r, "description contains HTML or entities")
    if len(d) < 200:
        note(r, "description under 200 chars")
    if r.get("work_type") not in WORK:
        fail(r, "work_type outside allowed set")
    lo, hi, per = r.get("salary_min"), r.get("salary_max"), r.get("salary_period")
    if per not in PERIODS:
        fail(r, f"salary_period not year/day/hour ({per})")
    if (hi is None) != (per is None) or (lo is not None and hi is None):
        fail(r, "salary period/max missing, or min without max")
    if lo is not None and float(lo) <= 0 or hi is not None and float(hi) <= 0:
        fail(r, "salary is zero or negative")
    if lo is not None and hi is not None:
        if float(lo) > float(hi):
            fail(r, "salary_min > salary_max")
        if per in RANGE and not (RANGE[per][0] <= float(lo) and float(hi) <= RANGE[per][1]):
            note(r, "salary outside plausible range for its period")
    if not r.get("company"):
        note(r, "company is null")
    pa = ts(r.get("posted_at"))
    if pa is None:
        note(r, "posted_at is null")
    elif pa > now:
        fail(r, "posted_at in the future")
    elif (now - pa).days > 120:
        note(r, "posted_at older than 120 days")
    fs, ls, rm = ts(r["first_seen_at"]), ts(r["last_seen_at"]), ts(r.get("removed_at"))
    if fs > ls:
        fail(r, "first_seen_at after last_seen_at")
    if rm and rm < fs:
        fail(r, "removed_at before first_seen_at")
    c = r.get("canonical_job_id")
    if c not in by_id:
        fail(r, "canonical_job_id points to no stored row")

groups = defaultdict(list)
for r in rows:
    groups[r["canonical_job_id"]].append(r)
multi = {c: g for c, g in groups.items() if len(g) > 1}
for c, g in multi.items():
    for r in g:
        if len({x["source"] for x in g}) < len(g):
            fail(r, "group has two rows from the same board")
        if len({(x["company"] or "").lower()[:6] for x in g}) > 1:
            note(r, "group members have different company names")

live = [r for r in rows if not r.get("removed_at")]
print(f"{len(rows)} rows, {len(live)} live, {len(rows) - len(live)} removed, "
      f"{len(multi)} duplicate groups covering {sum(len(g) for g in multi.values())} rows")
for s in ("reed", "linkedin", "indeed"):
    sr = [r for r in rows if r["source"] == s]
    n = len(sr) or 1
    fails = {id_ for ids in bad[s].values() for id_ in ids}
    print(f"\n== {s}: {len(sr)} rows | rows with a hard failure: {len(fails)} "
          f"({100 * (len(sr) - len(fails)) / n:.1f}% clean)")
    for label, rules in (("FAIL", bad[s]), ("warn", warn[s])):
        for rule, ids in sorted(rules.items(), key=lambda kv: -len(kv[1])):
            print(f"  {label}: {len(ids):4d}  {rule}   e.g. {', '.join(ids[:2])}")
    for k in ("company", "company_logo_url", "work_type", "salary_min", "posted_at"):
        have = sum(1 for r in sr if r.get(k) is not None)
        print(f"  filled {k}: {100 * have / n:.0f}%")
print("\nsalary periods:", dict(Counter(r["salary_period"] for r in rows)))
sys.exit(1 if any(bad[s] for s in bad) else 0)
