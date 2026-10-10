-include .env
export

.PHONY: db setup crawl test

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