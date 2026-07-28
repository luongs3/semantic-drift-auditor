PYTHON ?= python3
GMS ?= http://localhost:8080
FIXTURE ?= fixtures/showcase-drift.json

.PHONY: help install test audit demo drift record quickstart seed verify examples clean-demo

help:
	@echo "make demo        the 30-second version: audit a real catalog, no DataHub needed"
	@echo "make test        run the unit suite (no DataHub needed)"
	@echo "make install     install python deps"
	@echo ""
	@echo "  --- against a live instance ---"
	@echo "make quickstart  start DataHub OSS locally (docker, ~8GB RAM)"
	@echo "make seed        load the showcase-ecommerce sample catalog"
	@echo "make drift       author real definition changes through DataHub's API"
	@echo "make audit       detect drift on the live instance"
	@echo "make verify      audit + write findings back into the graph"
	@echo "make record      re-record the committed fixture"

install:
	$(PYTHON) -m pip install -r requirements.txt

test:
	$(PYTHON) -m pytest tests/ -q

# The judge path: no Docker, no DataHub, no setup. Real recorded catalog.
demo:
	@echo "Auditing a recorded snapshot of a real DataHub catalog — no instance required."
	@echo
	-@$(PYTHON) src/cli.py audit --fixture $(FIXTURE)

audit:
	-$(PYTHON) src/cli.py audit --gms $(GMS)

drift:
	$(PYTHON) src/cli.py drift --gms $(GMS)

verify:
	-$(PYTHON) src/cli.py audit --gms $(GMS) --write-back

record:
	$(PYTHON) src/cli.py record --gms $(GMS) --out $(FIXTURE)

# Regenerate everything in examples/ from the committed fixture, so the checked-in
# output is always something the code actually produced.
examples:
	-$(PYTHON) src/cli.py audit --fixture $(FIXTURE) \
	    --out examples/audit-report.md --json examples/findings.json
	@echo "examples/ regenerated"

quickstart:
	$(PYTHON) -m pip install --quiet 'acryl-datahub'
	$(PYTHON) -m datahub docker quickstart

seed:
	@# The first load can race GMS startup and write only a handful of events;
	@# running it again is idempotent and fills in the rest.
	$(PYTHON) -m datahub datapack load showcase-ecommerce
	@echo "Waiting for the search index to catch up..."
	@sleep 30

clean-demo:
	$(PYTHON) -m datahub docker nuke
