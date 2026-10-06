# -*- coding: utf-8 -*-
"""``python -m metaelemzes.headhunter <parancs> <projekt> [kapcsolók]`` — a parancssor a ``cli`` modulban van
(TERV 14. és 20.3 fejezet). Bekötés után: ``ma.py headhunter …`` (``metaelemzes.cli`` → ``cli.main(argv)``)."""
from __future__ import absolute_import

import sys

from .cli import main, build_parser  # noqa: F401  (a tesztek és a régi hívók innen is importálják)

if __name__ == "__main__":
    sys.exit(main())
