#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Az elemzés-képernyők fixture-jeinek újragenerálása — a VALÓDI motorból.

    python3 tests/gui/ui/analysis_fixtures/make.py [--check | --list]

Ez csak továbbhív a ``tests/gui/ui/gen_fixtures_from_engine.py``-ra (ugyanazokkal a kapcsolókkal). A korábbi,
kézzel formázó generátor (gen_fixtures.py + write_fixtures.py, tizedesvesszős szövegekkel) megszűnt: felülírta
volna a motorból generált ``analysis_*`` fixture-öket, és a ``tests/test_mvp_align.py`` sodródás-őre elbukott volna.
A ``analysis_kb.json`` (GET /api/kb/rules és /api/kb/item — a tudasbazis.sqlite valódi sorai) statikus fixture;
a motor számai nincsenek benne. A számok metafor-egyezését a motor tesztjei (tests/, tests/fuzz) igazolják.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GEN = os.path.join(os.path.dirname(HERE), "gen_fixtures_from_engine.py")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    return subprocess.call([sys.executable, GEN] + argv)


if __name__ == "__main__":
    sys.exit(main())
