#!/usr/bin/env nu
def main [--codegen] {
    if $codegen {
       print "Codegenning..."
       ./scripts/run_codegen.sh
    }

    print "Running with test_postgresql_async"
    ./tests/runtests.py async --settings test_postgresql_async --parallel=1 --debug-sql
    print "Running with test_sqlite"
    ./tests/runtests.py async --settings test_sqlite
    print "Running with test_postgresql"
    ./tests/runtests.py async --settings test_postgresql
}
