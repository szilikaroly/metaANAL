#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Az elemzés-képernyők fixture-jeinek újragenerálása (ma_gui/web/fixtures/analysis_*.json).

    python3 tests/gui/ui/analysis_fixtures/make.py [--check-metafor] [--keep]

1. gen_fixtures.py — a BCG-példa (peldak/bcg_oltas_RR.csv; row_uid, rob, estimated a kinyerés-fixtúrával
   összhangban) a motorral (metaelemzes.pipeline), hét változatban: elsődleges (REML + HKSJ, alcsoport:
   allokáció, kumulatív: év), elavult (régi adat: Aronson 1948 e1 = 6, HTML-darab a címkében), becsült nélkül,
   magas RoB nélkül, fix hatás, DL τ², kiugrók nélkül → szk.ma.plot/v2 nézetmodell, a motor szövegeivel.
2. write_fixtures.py — a borítékok (plot, runs, specs, jobs, kb) — a KB-tételek a tudasbazis.sqlite-ból.
3. --check-metafor: Rscript + metafor (check_metafor.R) → compare.py: a fő számok eltérése a metafortól.
A munkakönyvtár ideiglenes (MA_FIXTURE_WORK); a repóba csak a fixture-ök kerülnek.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    work = tempfile.mkdtemp(prefix='ma_fx_')
    env = dict(os.environ, MA_FIXTURE_WORK=work)
    for script in ('gen_fixtures.py', 'write_fixtures.py'):
        subprocess.run([sys.executable, os.path.join(HERE, script)], env=env, check=True)
    if '--check-metafor' in sys.argv:
        with open(os.path.join(work, 'metafor.json'), 'w') as fh:
            subprocess.run(['Rscript', os.path.join(HERE, 'check_metafor.R')], stdout=fh, check=True)
        subprocess.run([sys.executable, os.path.join(HERE, 'compare.py')], env=env, check=True)
    if '--keep' in sys.argv:
        print('munkakönyvtár:', work)
    else:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
