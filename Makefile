# ragformation — deterministic developer commands.
# Python 3.10+ standard library only; no virtualenv is required to run anything
# here. `make install` exists for people who prefer an isolated interpreter.

PY ?= python3
DATA ?= data

.PHONY: help init ingest ask serve test eval doctor stats clean rebuild check

help:
	@echo "ragformation targets"
	@echo "  make init      create the data directory and database"
	@echo "  make ingest    index the sample corpus in corpus/"
	@echo "  make ask Q=... ask one question on the command line"
	@echo "  make serve     run the HTTP API + web console (http://localhost:8787)"
	@echo "  make test      run the full test suite (stdlib unittest)"
	@echo "  make eval      run the golden evaluation set"
	@echo "  make doctor    diagnose providers, index integrity and configuration"
	@echo "  make stats     latency/quality summary from recorded queries"
	@echo "  make rebuild   re-embed stored chunks from the source of truth"
	@echo "  make clean     remove the local index (data/)"

init:
	$(PY) -m ragformation init

ingest:
	$(PY) -m ragformation ingest corpus

ask:
	@if [ -z "$(Q)" ]; then echo 'usage: make ask Q="How long are audit logs retained?"'; exit 2; fi
	$(PY) -m ragformation ask "$(Q)" --show-trace

serve:
	$(PY) -m ragformation serve --port $${RAG_PORT:-8787}

test:
	$(PY) -m unittest discover -s tests -p 'test_*.py' -v

eval:
	$(PY) -m ragformation eval

doctor:
	$(PY) -m ragformation doctor

stats:
	$(PY) -m ragformation stats

rebuild:
	$(PY) -m ragformation rebuild --force

clean:
	rm -rf $(DATA)
	@echo "removed $(DATA)/ — the next ingest rebuilds the index"

check: test eval
