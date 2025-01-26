#!/usr/bin/env sh
set -e
coverage erase
# coverage run ./runtests.py -k AsyncQuerySetTest -k AsyncNativeQuerySetTest -k test_acount --settings=test_postgresql --keepdb --parallel=1
STEPWISE=1 coverage run ./runtests.py --settings=test_postgresql --noinput || true #  --keepdb --parallel=1
coverage combine
# echo "Generating coverage for db/models/query.py..."
# coverage html --include '**/db/models/query.py'
echo "Generating coverage.."
coverage html --show-contexts # --include '**/db/models/query.py'
open coverage_html/index.html
