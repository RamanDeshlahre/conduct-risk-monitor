# One command per pipeline stage. `make all` builds everything from scratch.
PY := PYTHONPATH=src python -m crm.pipeline

.PHONY: setup test ingest pdfs enrich warehouse detect model note all refresh app

setup:
	python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

test:
	PYTHONPATH=src pytest -q

ingest:          ## FCA returns + full FOS listing crawl (hours the first time; resumable)
	$(PY) fca fos

pdfs:            ## optional: full text for a sample of decisions
	$(PY) pdfs

enrich:
	$(PY) enrich

warehouse:       ## load raw tables, build and test dbt models, export marts
	$(PY) load dbt export

detect:
	$(PY) detect

model:
	$(PY) model

note:
	$(PY) note

all: ingest enrich warehouse detect model note

refresh:         ## monthly: only new decisions, then rebuild everything downstream
	$(PY) fca fos-update enrich load dbt export detect model note

app:
	PYTHONPATH=src python app/app.py
