PY ?= python

.PHONY: setup verify run eval test all

setup:          ## create the venv and install dependencies
	$(PY) -m venv .venv && .venv/bin/python -m pip install -U pip -r requirements.txt

verify:         ## starter pre-flight (no API key needed)
	$(PY) verify_setup.py

run:            ## start the mock service and the reviewer console
	$(PY) run_local.py

eval:           ## run every suite against all three variants
	$(PY) evals/run_eval.py

test:           ## unit and safety tests
	$(PY) -m pytest tests/ -q

all: verify test eval
