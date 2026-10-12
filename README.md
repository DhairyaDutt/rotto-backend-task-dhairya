# UK Jobs Crawler

Crawls UK jobs from Reed, LinkedIn (guest pages) and Indeed UK into Postgres, links cross-board duplicates, tracks confirmed removals, and exports `output/jobs.jsonl` + `output/report.json`.

## Run it

```sh
cp .env.example .env     # add the proxy username/password from the brief (.env is gitignored)
make test                # offline lifecycle test, no proxy needed (uses a separate rotto_test DB)
make crawl               # live, incremental and idempotent: re-running updates, never duplicates
make audit               # offline check of output/jobs.jsonl (schema, salaries, dedupe sanity)
FIXTURE_PATH=tests/fixtures/edge_cases.json make test   # replay any fixture in the same format
```

Needs Python 3 and Docker (Postgres runs via Docker Compose). Logs: `logs/requests.jsonl` (every request: status, bytes, seconds, blocked/reason).

## How it works

1. **Search** a bounded set of keyword × location queries per board; **fetch details** only for new jobs or ones older than `REFRESH_DAYS` (5).
2. **Store** in one transaction per board: upsert details, mark seen/revive, then apply removals. `content_hash` ignores dates, so unchanged jobs are not rewritten.
3. **Removal is evidence-based.** Absence from search never removes a job. Unseen for `STALE_DAYS` (3) → checked directly (max `MAX_VERIFY` = 20 per run). Removed only on Reed "expired", or a 404/410 confirmed by a second request. Failed or blocked requests never remove. Indeed gives no reliable "gone" signal, so it is never removed. A removed job that reappears is revived.
4. **Dedupe** links copies across boards with a shared `canonical_job_id`; nothing is deleted. Matching: same company, similar title, compatible location, overlapping description. Same-board jobs are never merged (repeat postings are distinct jobs). Canonical IDs are voted so groups stay stable between runs. It favours missing a duplicate over wrongly merging two jobs.

## Bot protection: what worked and what didn't

- Requests use `curl_cffi` TLS impersonation through the UK residential proxy, with bounded retries and backoff on a fresh session. Blocks are detected by status (401/403/429/999) and challenge-page content, then logged.
- Reed and LinkedIn work with a Chrome profile. **Indeed returned 403 for every Chrome/Edge profile I tried** (chrome, chrome120, chrome124, edge101). A Safari profile plus a homepage warm-up works, so that is what Indeed uses.
- If Indeed blocks twice in a row the crawler stops that board and carries on with the others; one failing board never aborts the run.

## Limits and cost

`MAX_REQUESTS` (900 default, split per board), `MAX_MEGABYTES` (1200), `MAX_RETRIES` (3), `REQUEST_TIMEOUT` (30 s). The byte cap counts decompressed bodies, not the proxy's real billing.

**Estimated cost: roughly $0.30 to $0.70 per 1,000 requests**, depending on the mix (search pages are heavier than job pages). The committed `report.json` run was 127 requests, 16.7 MB estimated through the proxy, $0.08 at $5/GB, which is about $0.66 per 1,000. Over my whole development history (about 2,700 requests) the estimate was about $0.32 per 1,000. These are estimates from per-board compression ratios, not measured proxy figures; the provider dashboard is the source of truth.

## Trade-offs

- Sequential requests (~2 s each): simple and polite, but a large run takes hours. Coverage is bounded by the request budget.
- LinkedIn guest pages often omit salary and work type (`work_type` is set only when stated exactly). Indeed `work_type` and logo fill rates are lower for the same reason (source limits, not parser bugs).
- Indeed salaries use sentinels for missing bounds (-1 / 0); these are normalised to null.
- Dedupe is heuristic and cross-board only.
