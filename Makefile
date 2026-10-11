-include .env
export

.PHONY: db setup crawl test audit

# Start the database and WAIT until it reports "healthy"
db:
	docker compose up -d --wait

# Create a private Python environment and install the tools from requirements.txt
setup:
	python3 -m venv .venv
	.venv/bin/pip install -q -r requirements.txt

# THE MAIN COMMAND: start the database, install tools, run the crawler
crawl: db setup
	.venv/bin/python -m crawler.main

# Run the offline lifecycle tests (no proxy or internet needed for the tests themselves)
test: db setup
	.venv/bin/python -m tests.run_tests

# Offline check of output/jobs.jsonl against the brief's validity rules (no proxy)
audit:
	python3 tools/audit.py
