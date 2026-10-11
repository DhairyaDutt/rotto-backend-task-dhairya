# UK Jobs Crawler

A Python crawler that collects UK job listings from Reed, LinkedIn guest pages,
and Indeed UK. It stores listings in PostgreSQL, updates them on later runs,
links likely duplicates, tracks confirmed removals, and exports JSON files.

## What you need

- Python 3
- Docker with Docker Compose
- Internet access and valid proxy credentials to run a live crawl

The fixture tests do not need proxy credentials or internet access, but they do
need the local PostgreSQL container.

## Get started

1. Copy the example settings file:

   ```sh
   cp .env.example .env
   ```

2. Edit `.env` and replace the proxy username and password with the credentials
   from your assessment brief. Keep `.env` private; do not commit proxy
   credentials.

3. Run the offline lifecycle tests:

   ```sh
   make test
   ```

   This starts PostgreSQL and installs the Python dependencies if needed. The
   tests use the separate `rotto_test` database; each test run drops and
   recreates that database. They do not make proxy requests.

4. Run the live crawler:

   ```sh
   make crawl
   ```

   This starts PostgreSQL, installs dependencies if needed, and runs the
   crawler. Live crawling requires working proxy credentials in `.env`.

To replay a different fixture in the same format as the supplied lifecycle
fixture, run:

```sh
FIXTURE_PATH=tests/fixtures/edge_cases.json make test
```

## What a crawl does

For each configured job board, the crawler:

1. Searches a limited set of keyword-and-location combinations.
2. Finds listing IDs and fetches details for new listings or listings due for
   refresh.
3. Parses each site's page into a common job record.
4. Saves new or changed records in a database transaction.
5. Checks some older listings for removal evidence where supported.

Once the boards have been processed, likely duplicate listings are linked and
the output files are written.

### Refreshes and removals

- A job's details are fetched again after `REFRESH_DAYS` (5 by default) since
  its last detail check. Recently checked details are skipped.
- A live job not seen in search for `STALE_DAYS` (3 by default) can be checked
  directly, up to `MAX_VERIFY` (20) per supported board per run.
- A job is not removed just because it was absent from search results or a
  request failed. Removal requires confirmation from the job board. Reed and
  LinkedIn are checked for removals; Indeed is not, because it does not provide
  a reliable "job is gone" signal.
- A removed job found again is marked live.

## Duplicate linking

The crawler does not delete duplicate listings. It groups likely copies by
assigning them the same `canonical_job_id`, while retaining each original
listing and its source.

Matching considers normalized company names, similar titles, compatible
locations, and overlapping description text. Matches are joined into groups;
existing canonical IDs are preferred to keep groups stable across runs. This
is heuristic matching, so it favors avoiding incorrect merges over finding
every possible duplicate.

## Proxy use and request limits

The crawler routes requests through the configured proxy and has safety limits:

- `MAX_REQUESTS` (900 in `.env.example`), split across configured sources.
- `MAX_MEGABYTES` (1200 in `.env.example`), a cap on response-body bytes read
  by the crawler.
- `REQUEST_TIMEOUT` (30 seconds) and `MAX_RETRIES` (3).

Retries and the Indeed homepage warm-up count toward request use. Indeed uses a
Safari browser profile and a homepage warm-up because that worked more reliably
than the Chrome profile in the tested setup. Blocked responses and unexpected
page content are logged and retried with backoff. If one board fails, the
crawler reports it and continues with the others.

The byte cap is **not** a live measurement of the proxy provider's 10 GB quota.
The crawler sees response-body sizes, while proxy billing may count compressed
traffic and other bytes. `report.json` therefore includes a proxy-traffic
estimate based on per-board compression assumptions and the configured
`PROXY_USD_PER_GB` price. Check the provider dashboard for actual quota usage.

## Settings

Values in `.env` override the defaults. The main crawl settings in
`.env.example` are:

| Setting | Example value | What it controls |
| --- | ---: | --- |
| `SOURCES` | `linkedin,reed,indeed` | Which boards to crawl |
| `MAX_SEARCHES` | `12` | Search combinations per board |
| `REED_PAGES` | `2` | Result pages per Reed search |
| `INDEED_PAGES` | `2` | Result pages per Indeed search |
| `REFRESH_DAYS` | `5` | Minimum age of job details before refreshing |
| `STALE_DAYS` | `3` | How long unseen jobs wait before a verification check |
| `MAX_VERIFY` | `20` | Verification candidates per supported board per run |
| `MAX_REQUESTS` | `900` | Overall request safety limit |
| `MAX_MEGABYTES` | `1200` | Response-body byte safety limit, in MB |

The keyword and location lists are defined in `crawler/main.py`. The crawler
interleaves their combinations and only uses the first `MAX_SEARCHES` per board
in each run, keeping request use bounded.

## Output files

The crawler writes:

- `output/jobs.jsonl` — one JSON object per stored listing, including its source,
  job details, timestamps, removal status, and canonical ID.
- `output/report.json` — run times, request counts and statuses, job counts,
  duplicate-link count, and estimated proxy bytes and cost.
- `logs/requests.jsonl` — individual request attempts, including status,
  response size, duration, and failure or block reason.

The output files are replaced atomically so an interrupted write does not leave
a partially written file.

## Implementation notes and trade-offs

- Reed details are parsed from page JSON; LinkedIn from guest job-posting HTML;
  Indeed from embedded page JSON. Indeed detail requests are checked against the
  expected job key.
- LinkedIn guest pages can omit fields, so `work_type` is only set when the
  page states one exact value.
- Requests are sequential, which is simpler and more polite but makes a full
  run slower. Actual duration and coverage vary with results, retries, and
  blocking.
- Search depth is limited by the request budget. New or stale jobs require
  detail requests, so the number of jobs found affects how much of the budget
  remains for later work.
