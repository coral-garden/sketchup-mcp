#!/bin/sh
set -eu

python -m coverage erase
PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}" \
python -m coverage run scripts/run_python_coverage_tests.py
python -m coverage report
