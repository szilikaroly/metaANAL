#!/bin/bash
# ma-munkapad.command — a MA-munkapad indítója macOS-en (és Linuxon) (TERV 2.2).
# Használat: dupla kattintás a Finderben (rákérdez a projektmappára — a mappát a Terminál-ablakba húzhatod),
# vagy: ./ma-munkapad.command <projektmappa> [további „ma.py gui” kapcsolók, pl. --lang en]
# A Pythont ebben a sorrendben keresi: py -3, python, python3, .claude/.venv (a repó mappájában, majd a
# felhasználóéban). Minden jelöltet rövid szondával futtat, és csak a 0 kilépési kódú, legalább 3.9-es Pythont
# fogadja el (a macOS „python3” csonkja és a régi Python 2 így kiesik). Felülírás: MA_PYTHON=/út/python3
HERE="$(cd "$(dirname "$0")" && pwd)"
export PYTHONUTF8=1
PROBE='import sys; sys.exit(0 if sys.version_info[:2] >= (3, 9) else 3)'
PY=()

try_py() {
  "$@" -c "$PROBE" >/dev/null 2>&1 && PY=("$@")
}

[ -n "${MA_PYTHON:-}" ] && try_py "$MA_PYTHON"
[ ${#PY[@]} -eq 0 ] && command -v py >/dev/null 2>&1 && try_py py -3
[ ${#PY[@]} -eq 0 ] && command -v python >/dev/null 2>&1 && try_py python
[ ${#PY[@]} -eq 0 ] && command -v python3 >/dev/null 2>&1 && try_py python3
[ ${#PY[@]} -eq 0 ] && [ -x "$HERE/../.claude/.venv/bin/python" ] && try_py "$HERE/../.claude/.venv/bin/python"
[ ${#PY[@]} -eq 0 ] && [ -x "$HOME/.claude/.venv/bin/python" ] && try_py "$HOME/.claude/.venv/bin/python"

fail() {
  echo
  echo "$1"
  if [ -t 0 ]; then read -r -p "Nyomj Entert a bezáráshoz… " _; fi
  exit 1
}

if [ ${#PY[@]} -eq 0 ]; then
  fail "Nem találtam használható Python 3.9+ értelmezőt (python3, python, .claude/.venv). Telepítsd a Pythont a python.org-ról (lásd TELEPITES.md, 1. pont)."
fi

PROJ="${1:-}"
[ $# -gt 0 ] && shift
if [ -z "$PROJ" ]; then
  echo
  echo "MA-munkapad indítása"
  echo "Add meg a projektmappát: húzd ide a mappát a Finderből, vagy írd be az útját, majd nyomj Entert."
  read -r -p "Projektmappa: " PROJ
  # a Terminálba húzott út: záró szóköz, „\ ” a szóközök előtt, esetleg idézőjelek
  PROJ="${PROJ%"${PROJ##*[![:space:]]}"}"
  PROJ="${PROJ#"${PROJ%%[![:space:]]*}"}"
  PROJ="${PROJ//\\ / }"
  PROJ="${PROJ#[\'\"]}"
  PROJ="${PROJ%[\'\"]}"
fi
[ -z "$PROJ" ] && fail "Nem adtál meg projektmappát."
if [ ! -d "$PROJ" ]; then
  fail "Nincs ilyen mappa: $PROJ
Új projektet így hozhatsz létre: python3 \"$HERE/ma.py\" project init <mappa> --title \"Cím\""
fi

echo "A munkapad indul (${PY[*]}). Leállítás: ebben az ablakban Ctrl+C, vagy zárd be az ablakot."
"${PY[@]}" "$HERE/ma.py" gui --project "$PROJ" "$@" || fail "A munkapad hibával állt le (lásd fent)."
