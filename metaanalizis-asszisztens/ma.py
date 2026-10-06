# -*- coding: utf-8 -*-
"""Indító: python ma.py <parancs> ...  (bármelyik mappából hívható; telepítés nem kell)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from metaelemzes.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
