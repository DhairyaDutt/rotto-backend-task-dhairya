# Rotto backend task: UK jobs crawler

Crawls UK jobs from **Reed, LinkedIn (guest pages) and Indeed UK** into Postgres, links duplicates across boards, tracks changes and removals, and writes `output/jobs.jsonl` + `output/report.json`.

```
cp .env.example .env     # add the proxy username/password
make crawl               # starts Postgres, installs deps, crawls, writes output/
make test                # lifecycle fixture tests (no proxy needed)
```
`FIXTURE_PATH=path/to/fixture.json make test` runs any fixture in the same format.

## How it works
1. **Discover**: keyword x location searches per board (`MAX_SEARCHES` each). Each search page lists job ids.
2. **Fetch details**: one request per new job. Jobs whose detail was checked in the last `REFRESH_DAYS` (5) are skipped, so re-runs only pay for new or stale jobs.
3. **Parse**: Reed from the page's `__NEXT_DATA__` JSON; LinkedIn from the guest `jobPosting` HTML; Indeed from the embedded `_initialData` JSON (detail is requested as the search URL plus `&vjk=<jobkey>`, and the returned `jobKey` must match).
4. **Store** (`store.apply_run`): one transaction per source per run. New jobs are inserted; a changed `content_hash` (title, company, logo, location, work type, salary, description; dates excluded) updates the row. Running the same data twice changes nothing.
5. **Removal**: `removed_at` is set only with evidence: a 404/410 confirmed by a second request, or Reed's own "expired" flag. Absence from search, failed fetches, blocks and incomplete runs never remove anything. A job that reappears is un-removed. Indeed has no reliable "gone" signal, so Indeed jobs are never removed.
6. **Dedupe**: same company (normalised) + similar title + same place + overlapping description (5-word shingles), joined transitively; all copies share one stable `canonical_job_id`.
7. **Export**: `jobs.jsonl` (brief columns) and `report.json`, both written atomically.

## Bot protection
- `curl_cffi` with Chrome TLS impersonation (the TLS fingerprint is what Cloudflare on Indeed checks) through the rotating UK residential proxy.
- A response counts as blocked by **content**, not just status: 401/403/429/999, challenge-page titles, or missing expected markers. Blocked requests are retried with backoff on a fresh connection (new IP), up to `MAX_RETRIES`, and every attempt is logged (`logs/requests.jsonl`) and counted in the report.
- Hard brakes: `MAX_REQUESTS` (900, split evenly per board), `MAX_MEGABYTES`, `REQUEST_TIMEOUT`. One broken board never stops the others.

## Tradeoffs
- Depth is limited by request budget, not by parsing: more jobs = more detail requests (about 1 per job).
- Indeed blocks about 1 in 3 first attempts; retries absorb it at the cost of extra requests.
- LinkedIn guest pages hide some fields, so `work_type` is only filled when the page states one exact value.
- Dedupe is heuristic (precision over recall): a wrongly merged pair is worse than a missed one.
- Sequential requests (about 2 s each, ~12 min for a full run) for simplicity and politeness.

## Cost per 1,000 pages
From a measured run (448 requests, 143.6 MB unpacked): estimated **about 63 KB through the proxy per page, so about 63 MB and roughly $0.32 per 1,000 pages** at an assumed $5/GB. The proxy bandwidth is estimated as unpacked size x a per-board compression ratio (Reed 0.40, LinkedIn 0.12, Indeed 0.18) and `PROXY_USD_PER_GB`; both are assumptions, not measured from the proxy dashboard. Worst case with no compression is about 320 KB/page, so about $1.60 per 1,000 pages.

## Refresh policy
- A job's detail page is re-fetched only after `REFRESH_DAYS` (5) since its last detail check; seen-in-search just advances `last_seen_at`.
- Live jobs not seen for `STALE_DAYS` (3) are verified directly (up to `MAX_VERIFY` per board per run) before any removal.
- Run `make crawl` daily: new jobs cost 1 request each, existing ones cost nothing until they go stale.
