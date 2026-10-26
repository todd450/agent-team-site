#!/bin/sh
# Every check this product runs; CI runs this on every PR.
set -e
python3 -m unittest discover -s tests
