# -*- coding: utf-8 -*-
"""Metaheadhunter — meglévő metaanalízisek bányászata (szk.ma.headhunter/v1).

Kezdőknek: a csomag a témában már megjelent szisztematikus áttekintéseket és metaanalíziseket keresi meg
(PubMed, Europe PMC, opcionálisan OpenAlex és Scopus), kinyeri belőlük a *bevont* vizsgálatokat
bizonyítékkal, azonosítja és duplumszűri őket, majd egyetlen egyesített vizsgálatlistát és PRISMA 2020
számokat ad, és dátumkorlátos frissítő keresést futtat. A tervet és a szerződéseket lásd:
``TERV_metaheadhunter.md`` és ``metaelemzes/headhunter/contracts/``.

Parancs: ``python -m metaelemzes.headhunter …`` (bekötés után ``ma.py headhunter …``).
Forrás-ellenőrzés: ``python -m metaelemzes.headhunter sources --check``
(tartalék belépési pont: ``python -m metaelemzes.headhunter.sources --check``).

A csomag importja szándékosan könnyű: semmilyen almodult nem tölt be (a hálózati réteg, a forráskliensek és
a folyamat-modulok külön, lustán importálhatók).
"""

__version__ = "1.0.0"

MODEL = "szk.ma.headhunter/v1"

__all__ = ["__version__", "MODEL"]
