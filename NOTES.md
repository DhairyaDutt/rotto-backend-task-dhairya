# Recon and build notes

## Status

| Board | List | Detail | Fields | Dead job | Notes |
|---|---|---|---|---|---|
| Reed | Done | Done | Done | Done (410) | No blocking seen. Parser written, test pending |
| LinkedIn | Done | Done | Done | Done (404) | Salary on a salaried job still to check |
| Indeed | Done | Done (via vjk) | Mostly done | Not tested | Works with Chrome fingerprint. About 80% success on 5 requests |

| Build phase | Status |
|---|---|
| 0 Setup | Done |
| 1 Recon | Done |
| 2 Fetch layer (config, request log, fetch) | Done, tested on 4 requests, committed |
| 3 Parsers | Reed written (untested), LinkedIn and Indeed to do |
| 4 Store and lifecycle, 5 make test, 6 Dedupe, 7 Scale, 8 Deliverables | To do |

## Working rules

- Data quality matters more than volume. Accept a page only when its content matches the expected job ID.
- A 200 status is not proof of success. Check the content.
- Never guess a field. Use null when the source does not say.
- Removal needs explicit evidence (see "Dead jobs"). Absence, blocks and errors never count.

---

## Reed

### Discovery
- Search page: https://www.reed.co.uk/jobs, about 27 jobs per page, about 116 KB per page.
- Job URL: /jobs/<title>/<id>. The number is source_job_id.
- Links without a trailing number (e.g. /jobs/reed/) are company pages, not jobs.

### Detail
- URL: https://www.reed.co.uk/jobs/<title>/<id>, about 120 KB.
- Data: __NEXT_DATA__ script, props.pageProps.consolidatedJobDetails.jobDetails.
- Ignore similarJobs in the same block (they have invented salary ranges and isLive false).
- Browser redirect to /application?returnUrl=... is JavaScript only. Server returns 200, no redirect.

### Fields
- id, title, description (HTML), createdDate, displayDate, updatedDate, expiryDate, isLive
- jobOwner.profileName / ouName (company), jobOwner.profileLogoImage (filename only)
- jobLocation (locationName, countyName, countryCode, isRemoteJob)
- jobSalary (displaySalary, from, to, type.name)
- jobEmploymentHours (isFullTime, isPartTime), isContract, isTemp, jobContractType

### Parser decisions (crawler/sources/reed.py)
- Accept only if jobDetails.id equals the requested ID. Else bad_page.
- isLive false -> state "expired" (evidence only, needs second confirmation).
- countryCode must be GB, else not_uk.
- Salary stored only if displaySalary is present and period is year/day/hour. Else null.
- work_type: contract, temporary, part time, full time, else null. "Permanent" has no slot.
- posted_at uses createdDate (assumed UK time). Not part of content_hash.
- Reject if title, location or description is empty.

### Unverified (check against the live page)
- Logo URL base: built as resources.reed.co.uk/profileimages/logos/thumbs/<file>. This is a guess.
- Which date Reed shows as "posted": createdDate (21 Sep) vs displayDate (10 Oct).
- Displayed location text vs "Hartlepool, Cleveland".
- Company on agency listings (profileName vs ouName).

### Cost
- About 4 KB discovery + 120 KB detail = about 124 KB per job.

---

## LinkedIn

### Discovery
- Search: /jobs/search/?keywords=X&location=Y, about 28 KB, about 60 jobs, 200 on 6 of 6 requests.
- Page title shows the total, e.g. "11,000+ Sales jobs in United Kingdom". Paging is capped (about 1000), so slice by keyword x location.
- Job ID: number at the end of /jobs/view/<slug>-<ID>, or data-entity-urn="urn:li:jobPosting:<ID>".

### Detail
- Endpoint: /jobs-guest/jobs/api/jobPosting/<ID>, about 8.6 KB.
- HTML fragment. No <title>, no ld+json.

### Fields
- title: .top-card-layout__title
- company: a.topcard__org-name-link (text)
- location: span.topcard__flavor--bullet (not the first topcard__flavor, that is the company)
- logo: img.artdeco-entity-image, URL in data-delayed-url (decode &amp; to &)
- description: div.show-more-less-html__markup (HTML -> plain text, keep line breaks)
- salary: absent on the sample job -> null. Check a salaried job.
- work type: find the h3 "Employment type", read the span below it. Map only exact Full-time / Part-time / Contract / Temporary. Sample says "Internship" -> null.

### Posted date
- Preferred: search card <time class="job-search-card__listdate" datetime="YYYY-MM-DD">. Read the attribute, from the same card as the job ID.
- Fallback: job page span.posted-time-ago__text ("1 day ago", text follows a line break). posted_at = fetch time minus that amount.
- Posted text is not in content_hash. posted_at is not overwritten on later runs.
- Job age never implies removal (8-month-old jobs still appear).

### Cost
- About 0.5 KB discovery + 8.6 KB detail = about 9 KB per job.

---

## Indeed

### What does not work
- Plain curl via proxy: /jobs?q=... -> 403, about 10 KB, "Blocked - Indeed.com" (Cloudflare).
- /viewjob?jk=<id> -> 401 "Authenticating...", a redirect to login with from=bot-detection-anonymous.
- /viewjob?...&from=serp -> 403 "Security Check".
- Cookies and a referer did not help. Do not try more /viewjob variants.

### What works
- curl_cffi with impersonate="chrome" through the same proxy -> 200, real search page. The block was the TLS fingerprint, not the IP.
- Search page with &vjk=<jk> -> 200, about 1.14 MB unpacked. It embeds the detail of that job.

### Where the data is
- Detail: window._initialData.autoOpenTwoPaneViewjobResponse.body
  - body.jobKey (must equal the requested jk)
  - jobInfoWrapperModel.jobInfoModel.sanitizedJobDescription (HTML)
  - jobInfoHeaderModel: jobTitle, companyName, formattedLocation
  - jobMetadataHeaderModel: jobType
  - jobDescriptionSectionModel.jobDetailsSection: isJobExpired, salaryInfoModel (null on the sample)
  - datePublished exists (value not checked)
- Ignore the top-level autoOpenTwoPaneJobKey. It showed a different ID from the requested one.
- Cards (15 per page): window.mosaic.providerData["mosaic-provider-jobcards"].metaData.mosaicProviderJobCardsModel.results
  - jobkey, displayTitle, company, formattedLocation, salarySnippet.text, jobTypes, createDate (ms), formattedRelativeTime, snippet (teaser only)
- Card data alone is not a valid job (no full description).

### Test results
- vjk on 5 jobs in one session: 4 x 200 with matching jobKey and description (3.0 to 4.9k chars), 1 x 403 "Security Check" (28 KB) on the first request. Caught by the jobKey check.
- Hypothesis (untested): a plain search request first, to warm the session, avoids the 403.

### Open questions
- Real proxy cost (not measured, server uses brotli).
- Success rate at scale.
- Dead-job signal (isJobExpired and what a removed job returns).
- Salary currency: one card showed USD, so check GBP before storing a salary.
- Salary is often empty; work type is often empty (null).
- Meaning of the "sponsored" flag on all cards.
- Meaning of createDate (post date or refresh).

### Cost (estimates)
- Unpacked about 1.14 MB. Gzip estimate about 200 KB. Likely 150 to 200 KB per request.
- Each vjk request gives 1 full job plus 15 cards.

---

## Cost per job (estimates)

| Board | Per job | 10 GB covers about |
|---|---|---|
| LinkedIn | about 9 KB | over 1 million |
| Reed | about 124 KB | about 80,000 |
| Indeed | about 150 to 200 KB | about 50,000 to 65,000 |

---

## Dead jobs

### Signals seen
- Reed: GET /jobs/job/<id> for an unknown ID -> 410, about 147 KB body. Live pages also carry isLive and expiryDate.
- LinkedIn: guest endpoint for an unknown ID -> 404, 0 bytes.
- Indeed: not tested.

### Rules
- Removal needs explicit evidence (404, 410, isLive false, isJobExpired true), confirmed by a second request on a fresh IP.
- Never removal evidence: 401, 403, 429, 5xx, timeouts, challenge or login pages, absence from search results, job age.
- fetch.py returns 404 and 410 as "gone" without retrying. The confirmation and the removed_at decision belong in the store code.
- Still to verify: a job that was live and later expired, not just a never-existing ID.

---

## Fetch layer (Phase 2)

- crawler/config.py: reads PROXY_URL or PROXY_HOST / PROXY_USERNAME / PROXY_PASSWORD (+ PROXY_PORT), DATABASE_URL, MAX_REQUESTS, MAX_MEGABYTES, MAX_RETRIES, REQUEST_TIMEOUT.
- crawler/requestlog.py: one JSONL line per attempt (source, kind, url, attempt, status, bytes, seconds, blocked, reason). Feeds report.json.
- crawler/fetch.py: Fetcher.get(source, kind, url, validate). Chrome fingerprint, retries with backoff and jitter, new session per retry, block detection by status, title and the board's validate check, request and byte limits before every attempt.
- Test: 4 requests (Reed job, LinkedIn job, LinkedIn dead job, Indeed search), 0 blocked, 1.5 MB unpacked.
- Retry path not yet exercised (nothing failed). Test it by forcing a failure.
- Byte limits use the unpacked size, so they overstate real proxy usage (safe direction).

---

## Open items

- Test the Reed parser on cache/reed_job.html and verify the four unverified points.
- Write the LinkedIn and Indeed parsers.
- Check a LinkedIn job with a salary.
- Indeed: dead-job signal, warm-up hypothesis, salary currency.
- Pick final default limits for the graders' make crawl once real cost is known.
- Move the useful parts of these notes into the one-page README at the end.