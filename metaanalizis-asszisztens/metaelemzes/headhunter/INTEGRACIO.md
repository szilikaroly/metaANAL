# Metaheadhunter — bekötés (integrátornak; TERV 21. fejezet)

A `metaelemzes/headhunter/` csomag önállóan fut (`python -m metaelemzes.headhunter …`). Az alábbi pontok a **más
munkaágakhoz tartozó** fájlokba kötik be. Minden pont másolható; a sorrend szabad, de az 1–3. nélkül a `ma.py
headhunter …` és a munkapad-képernyő nem érhető el.

Belépési pontok a csomagban:

| Mire | Hívás |
|---|---|
| parancssor | `metaelemzes.headhunter.cli.main(argv) -> int` (0 rendben · 1 hiba · 2 használati hiba · 3 forrás részleges · 4 emberi döntésre vár) |
| parancs kiírás nélkül | `metaelemzes.headhunter.cli.execute(argv) -> (envelope, args)` |
| Python-homlokzat | `metaelemzes.headhunter.facade`: `init`, `sources_status`, `run_step`, `decide`, `status`, `verify`, `rebuild`, `show_text` (mind JSON-boríték: `ok, command, data, warnings, errors, pending, next, exit_code`) |
| gépi ellenőrzések | `metaelemzes.headhunter.checks`: `RULES`, `RULE_STAGES`, `KB_REFS`, `verify(project_dir, for_analysis=False)` |

## 1. `metaelemzes/cli.py` — `headhunter` alparancs

A `kettos` mintájára (a modul-docstring parancslistájába is egy sor):

```python
# ------------------------------------------------------------ headhunter (Metaheadhunter)
HEADHUNTER_NAMES = ("headhunter", "metaheadhunter")


def _headhunter_subcommand(argv):
    """argv[0] = headhunter → a Metaheadhunter parancssora (metaelemzes.headhunter.cli.main); más parancsnál None."""
    if not argv or argv[0] not in HEADHUNTER_NAMES:
        return None
    from .headhunter import cli as hh_cli
    return hh_cli.main(list(argv[1:]))
```

`main()`-ben a `_kettos_subcommand` hívása mellé:

```python
    sub_rc = _headhunter_subcommand(list(argv) if argv is not None else sys.argv[1:])
    if sub_rc is not None:
        return sub_rc
```

Docstring-sor: `headhunter               Metaheadhunter — meglévő metaanalízisek bányászata (find-reviews, extract,
resolve, dedupe, overlap, screen, update-search, merge, prisma, signoff …)`.

## 2. `metaelemzes/api.py` — homlokzat-függvények

```python
def _hh():
    from .headhunter import facade
    return facade


def headhunter_status(project_dir):
    return _hh().status(project_dir)


def headhunter_sources(project_dir=None, check=False):
    return _hh().sources_status(project_dir, check=check)


def headhunter_run_step(project_dir, step, **options):
    return _hh().run_step(project_dir, step, **options)


def headhunter_decide(project_dir, kind, target, value, actor, **kw):
    return _hh().decide(project_dir, kind, target, value, actor, **kw)


def headhunter_verify(project_dir, for_analysis=False):
    return _hh().verify(project_dir, for_analysis=for_analysis)
```

A képesség-leírásba (`capabilities()` / `ENGINE_COMMANDS`, ha van `features` lista): `"headhunter"`; a csomag
importálhatósága az állapot (`ok`), a források állapota `headhunter_sources()`-ból (TERV 21/15).

## 3. `ma_gui/routes/__init__.py`

```python
from . import headhunter  # noqa: E402 — Metaheadhunter (meglévő metaanalízisek bányászata)
MODULES += (headhunter,)
```

## 4. Skill és plugin

- `.claude/skills/metaanalizis/SKILL.md` (és a plugin-másolat `metaanalizis-asszisztens/skills/metaanalizis/SKILL.md`):
  az alágensek közé `ma-metaheadhunter` (plugin-névvel `metaanalizis:ma-metaheadhunter`). Mikor hívd: S01
  duplikáció-ellenőrzés után, ha a témában van meglévő SR/MA; S03–S04 keresés és szűrés. Röviden: L1 felkutatás →
  EP1 kiválasztás → L3 kinyerés (bizonyítékkal) → EP2 → L4 feloldás (API) → L5 duplumok → EP3 → L6 átfedés (CCA) →
  L7 szűrés → EP4 → L8 frissítő keresés (ablak-jóváhagyás) → L9 egyesítés + PRISMA → EP5 lezárás → EP6 másodlagos
  adatok ellenőrzése. Kilépési kódok: 0/1/2/3/4 (fent).
- Plugin-újragenerálás: `python metaanalizis-asszisztens/tools/build_plugin.py` (az új ágensfájl miatt a
  `tests/test_mvp_plugin.py` két tesztje addig drift-et jelez), majd a `test_mvp_plugin.GeneratedFilesTest`
  elvárt listájába `"ma-metaheadhunter"`.

## 5. Tudásbázis és projektnapló (opcionális, TERV 21/4–5)

- `metaelemzes/kb.py`: `ENGINE_RULESETS` += `("headhunter.checks", "S03")`; `ENGINE_RULE_KINDS["headhunter.checks"] =
  "Metaheadhunter"`; a `RULES` formátuma a `validate.RULES`/`prisma.RULES`-é.
- `ma_gui/snapshot.py` `_KB_ID_RE` és a `contracts/common.v1` `kbid` mintája: `[VPX]\d{3}` → `[VPXH]\d{3}`.
- `metaelemzes/projekt.py` `KNOWN_AGENTS` += `"headhunter"` (addig az EP5-tükör `agent="planner"`).

## 6. Engedélyek, dokumentáció, .gitignore (a felhasználó jóváhagyásával)

- `.claude/settings.json` engedélylista: `Bash(python3 -m metaelemzes.headhunter:*)`,
  `Bash(python -m metaelemzes.headhunter:*)`, `WebFetch(domain:api.elsevier.com)` — ez a felhasználó döntése.
- `.gitignore`: `**/01_kereses/headhunter/cache/` és `**/01_kereses/headhunter/runs/` (az `init` helyi `.gitignore`-t
  is ír).
- `TELEPITES.md` / `ESZKOZOK_ES_HOZZAFERESEK.md`: környezeti változók (`MA_CONTACT_EMAIL`, `MA_OPENALEX_APIKEY`,
  `MA_SCOPUS_APIKEY`, `MA_SCOPUS_INSTTOKEN`, `MA_NCBI_APIKEY`), ellenőrzés: `python -m metaelemzes.headhunter sources
  --check`; Scopus élő igazolása a saját gépen:
  `MA_SCOPUS_APIKEY=… python -m metaelemzes.headhunter sources --check --sources scopus`, majd
  `python3 tests/reference/headhunter/cassettes/record_cassettes.py --only scopus_live`.
- Audit-csomag (`ma_gui/audit_export.py`): a `cache/` és a `runs/*/CANCEL` kizárása; a `decisions.jsonl` és a
  `prisma_flow.json` bevétele.

## 7. Ellenőrzés a bekötés után

```bash
python3 ma.py headhunter --help
python3 -m unittest discover -s tests -p 'test_headhunter*'
python3 -m unittest discover -s tests/gui -p 'test_headhunter*'
node tests/gui/ui/headhunter.spec.js        # előtte: python3 ma_gui/web/build_gui.py --dev
python3 metaanalizis-asszisztens/tools/build_plugin.py --check
```
