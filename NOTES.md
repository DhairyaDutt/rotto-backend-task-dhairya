# Recon Notes

## Status summary

| Board | List | Detail | Fields | Dead job | Notes |
|---|---|---|---|---|---|
| Reed | Done | Done | Done | Done (410) | No blocking issue seen |
| LinkedIn | Done | Done | Done | Done (404) | Salary still needs a check on salaried jobs |
| Indeed | Not started |  |  |  | Hardest target; 3-hour limit |

## Working assumptions

- We are scraping public job listings and validating parsing rules against live pages.
- Data quality matters more than raw volume: we only accept a page when the payload matches the expected job identity.
- Removal should be based on explicit evidence, not on a missing listing or a generic rate-limit response.

---

## Reed

### Discovery and page shape

- Search page: https://www.reed.co.uk/jobs
- Approximate size: ~27 jobs per page, ~116 KB per page
- Job URL pattern: /jobs/<title>/<id>
  - The ID is the source_job_id.
  - Links without a trailing numeric ID (for example /jobs/reed/) are company pages, not job pages.

### Detail page

- Job detail URL shape: https://www.reed.co.uk/jobs/<title>/<id>
- Approximate size: ~120 KB
- Data is embedded in the __NEXT_DATA__ script.
- Target location: props.pageProps.consolidatedJobDetails.jobDetails
- Ignore similarJobs from the same block; it is not the canonical job payload.

### Fields to extract

- id
- title
- description (HTML)
- jobOwner.ouName
- jobOwner.profileLogoImage (filename only; build the full URL later)
- jobLocation
- jobSalary
- jobEmploymentHours
- jobContractType
- createdDate
- expiryDate
- isLive

### Anti-bot / browser behavior

- No bot protection has been observed so far; requests return HTTP 200.
- Browser redirects job pages only through client-side JavaScript to /application?returnUrl=...
- The server still responds with HTTP 200 and does not perform a server-side redirect.
- This means the crawler is not affected by that redirect flow.

### Safety / validation rules

- Only accept a page when __NEXT_DATA__ contains jobDetails and the payload matches the expected job ID.
- Do not trust similarJobs or generic page content.

### Cost estimate

- Discovery: ~4 KB per job
- Detail fetch: ~120 KB per job
- Total: ~124 KB per job

---

## LinkedIn

### Discovery and search flow

- Search endpoint: /jobs/search/?keywords=X&location=Y
- Approximate size: ~28 KB
- Typical result count: ~60 jobs per page
- HTTP status: 200 on 6/6 test requests
- The total count in the page title is a summary value such as "11,000+ Sales jobs in the United Kingdom".
- Paging is capped around 1000 results, so the practical strategy is to slice by keyword + location.

### Job identity

- Job ID appears at the end of /jobs/view/<slug>-<ID>
- Or as data-entity-urn="urn:li:jobPosting:<ID>"

### Detail endpoint

- Endpoint: /jobs-guest/jobs/api/jobPosting/<ID>
- Approximate size: ~8.6 KB
- This is an HTML fragment without a <title> tag and without ld+json metadata.

### Fields to extract

- title: .top-card-layout__title
- company: a.topcard__org-name-link (text)
- location: span.topcard__flavor--bullet
  - Important: do not use the first topcard__flavor, because that is the company field, not the location.
- logo: img.artdeco-entity-image, URL in data-delayed-url
  - Decode &amp; to & before use.
- description: div.show-more-less-html__markup
  - Keep the HTML-to-text conversion and preserve line breaks.
- salary: currently null on the sample job; a salaried example still needs checking later.

### Employment type parsing

- Find the h3 text "Employment type" and read the span below it.
- Allowed values can only be mapped exactly to:
  - Full-time
  - Part-time
  - Contract
  - Temporary
- Example: an internship listing should be treated as null, not as a valid employment type.

### Posted date rules

Preferred source:

- Search card: <time class="job-search-card__listdate" datetime="YYYY-MM-DD">
- Read the datetime attribute, not the human-readable text like "8 months ago".
- The datetime must come from the same card as the job ID.

Fallback source:

- Job page: span.posted-time-ago__text, such as "1 day ago"
- posted_at = fetch time minus that amount

Important rules:

- Posted text must not be included in content_hash.
- posted_at must not be overwritten on later runs.
- A job being older does not imply removal; 8-month-old jobs still appear in search results.

### Cost estimate

- Discovery: ~0.5 KB per job
- Detail fetch: ~8.6 KB per job
- Total: ~9 KB per job

---

## Dead job detection rules

### Reed

- Requesting /jobs/job/<id> for an unknown or stale ID returns HTTP 410 with a ~147 KB body.
- A live page may also contain isLive: false or an expiryDate value.

### LinkedIn

- Requesting /jobs-guest/jobs/api/jobPosting/<id> for an unknown ID returns HTTP 404 with an empty body.

### Required criteria for removal

- Removal requires explicit evidence such as:
  - HTTP 404 or 410, or
  - isLive: false
- This evidence should be confirmed with a second request from a fresh IP before marking the job as removed.

### Not valid as removal evidence

These are never enough by themselves:

- HTTP 403
- HTTP 429
- 5xx responses
- Timeouts
- Challenge or login pages
- Absence from search results

### Still to verify

- A job that was once live and later expired, not just a never-existing ID.

---

## Indeed

### Current status

- The board is not yet complete for Indeed.
- This is the hardest target and has a 3-hour limit.

### Observed behavior

- /viewjob?jk=... often returns 401 "Authenticating..." with bot-detection flow and redirect-to-login behavior.
- A 403 "Security Check" response can also appear from serp.
- Cookies and referer headers did not help.

### Search-page workaround

- Search page with &vjk=<jk> returns HTTP 200 and a ~1.14 MB unpacked payload.
- The relevant job data is embedded in:
  - window._initialData.autoOpenTwoPaneViewjobResponse.body
  - jobInfoWrapperModel.jobInfoModel
  - sanitizedJobDescription (HTML)
  - jobInfoHeaderModel.jobTitle / companyName / formattedLocation
  - jobMetadataHeaderModel.jobType
  - isJobExpired in jobDescriptionSectionModel.jobDetailsSection

### Validation rules

- Accept only when body.jobKey equals the requested jk.
- Ignore the top-level autoOpenTwoPaneJobKey; it is not the reliable identifier.

### Card data

- Cards on the search page appear at ~15 per page.
- Data is embedded in:
  - window.mosaic.providerData["mosaic-provider-jobcards"].metaData.mosaicProviderJobCardsModel.results
- Fields available include:
  - jobkey
  - displayTitle
  - company
  - formattedLocation
  - salarySnippet

### Fingerprinting and bot protection

- The only approach that appears to get past Cloudflare is curl_cffi with impersonate=chrome.
- Plain curl fails with 403.
- Open questions: real proxy cost, repeat success rate, and whether vjk works for jobs outside the current search result page.

### vjk test result

- 5 jobs tested in the same session
- 4 requests succeeded with HTTP 200 and matching jobKey + description content (3.0-4.9k characters)
- 1 request failed with HTTP 403 and a "Security Check" response near the first attempt
- This was caught by the jobKey validation check.
- Hypothesis: warming the session with a plain search request may reduce the risk of the security gate.

### Cost estimate

- Unpacked page size: ~1.14 MB
- Gzip estimate: ~200 KB
- Server appears to use br compression; the true on-the-wire cost is likely ~150-200 KB, not measured directly

### Relative cost per job

- LinkedIn: ~9 KB
- Reed: ~124 KB
- Indeed: ~150-200 KB (estimates)

---

## Final notes

- Reed is the cleanest target: no bot interference and straightforward __NEXT_DATA__ extraction.
- LinkedIn is feature-complete and practical, with one remaining salary check on salaried jobs.
- Indeed is still the main challenge because of bot detection and Cloudflare gating.
