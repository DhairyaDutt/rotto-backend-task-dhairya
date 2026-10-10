CREATE TABLE IF NOT EXISTS jobs (
    id                    text PRIMARY KEY,
    source                text NOT NULL,
    source_job_id         text NOT NULL,
    url                   text,
    title                 text,
    company               text,
    company_logo_url      text,
    location              text,
    work_type             text,
    salary_min            numeric,
    salary_max            numeric,
    salary_period         text,
    posted_at             timestamptz,
    description           text,
    canonical_job_id      text,
    content_hash          text,
    first_seen_at         timestamptz NOT NULL,
    last_seen_at          timestamptz NOT NULL,
    removed_at            timestamptz,
    -- extra columns for our own bookkeeping
    last_detail_checked_at timestamptz,
    gone_checks           integer NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS jobs_source_idx ON jobs (source);
CREATE INDEX IF NOT EXISTS jobs_canonical_idx ON jobs (canonical_job_id);
CREATE INDEX IF NOT EXISTS jobs_removed_idx ON jobs (removed_at);