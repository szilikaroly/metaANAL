@echo off
rem ma-munkapad.cmd - a MA-munkapad inditoja Windowson (TERV 2.2).
rem Hasznalat: dupla kattintas (rakerdez a projektmappara), vagy huzd a projektmappat erre a fajlra,
rem vagy parancssorbol: ma-munkapad.cmd "C:\...\projekt" [tovabbi "ma.py gui" kapcsolok, pl. --lang en]
rem A Pythont ebben a sorrendben keresi: py -3, python, python3, .claude\.venv (a repo mappajaban, majd a
rem felhasznalo mappajaban). A Windows Store python.exe-csonkjat kiszuri: minden jeloltet egy rovid
rem szondaval futtat, es csak a 0 kilepesi kodu, legalabb 3.9-es Pythont fogadja el (a csonk nem 0-val lep ki).
rem Feluliras: set MA_PYTHON=C:\ut\python.exe
setlocal EnableExtensions DisableDelayedExpansion
set "_OLDCP="
for /f "tokens=2 delims=:" %%a in ('chcp') do set "_OLDCP=%%a"
if defined _OLDCP set "_OLDCP=%_OLDCP: =%"
if defined _OLDCP set "_OLDCP=%_OLDCP:.=%"
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "HERE=%~dp0"
set "PROBE=import sys; sys.exit(0 if sys.version_info[:2] >= (3, 9) else 3)"
set "PYEXE="
set "PYARG="

if defined MA_PYTHON (
  "%MA_PYTHON%" -c "%PROBE%" >nul 2>&1 && set "PYEXE=%MA_PYTHON%"
)
if not defined PYEXE (
  py -3 -c "%PROBE%" >nul 2>&1 && (set "PYEXE=py" & set "PYARG=-3")
)
if not defined PYEXE (
  python -c "%PROBE%" >nul 2>&1 && set "PYEXE=python"
)
if not defined PYEXE (
  python3 -c "%PROBE%" >nul 2>&1 && set "PYEXE=python3"
)
if not defined PYEXE if exist "%HERE%..\.claude\.venv\Scripts\python.exe" (
  "%HERE%..\.claude\.venv\Scripts\python.exe" -c "%PROBE%" >nul 2>&1 && set "PYEXE=%HERE%..\.claude\.venv\Scripts\python.exe"
)
if not defined PYEXE if exist "%USERPROFILE%\.claude\.venv\Scripts\python.exe" (
  "%USERPROFILE%\.claude\.venv\Scripts\python.exe" -c "%PROBE%" >nul 2>&1 && set "PYEXE=%USERPROFILE%\.claude\.venv\Scripts\python.exe"
)
if not defined PYEXE goto :nopython

set "PROJ=%~1"
if not "%PROJ%"=="" shift
if "%PROJ%"=="" (
  echo.
  echo MA-munkapad indítása
  echo Add meg a projektmappát: húzd ide a mappát az Intézőből, vagy másold be az útját, majd nyomj Entert.
  set /p "PROJ=Projektmappa: "
)
if not defined PROJ goto :noproject
set "PROJ=%PROJ:"=%"
if "%PROJ%"=="" goto :noproject
if not exist "%PROJ%\" goto :nodir

set "REST="
:collect
if "%~1"=="" goto :run
set REST=%REST% %1
shift
goto :collect

:run
echo A munkapad indul (%PYEXE% %PYARG%). Leállítás: ebben az ablakban Ctrl+C, vagy zárd be az ablakot.
"%PYEXE%" %PYARG% "%HERE%ma.py" gui --project "%PROJ%" %REST%
if errorlevel 1 goto :fail
goto :end

:nopython
echo.
echo Nem találtam használható Python 3.9+ értelmezőt (py -3, python, python3, .claude\.venv).
echo Telepítsd a Pythont a python.org-ról (a telepítőben pipáld be: "Add python.exe to PATH"),
echo lásd TELEPITES.md, 1. pont. A Microsoft Store "python" parancsa önmagában csak egy telepítő-csonk.
goto :fail

:noproject
echo Nem adtál meg projektmappát.
goto :fail

:nodir
echo.
echo Nincs ilyen mappa: "%PROJ%"
echo Új projektet így hozhatsz létre: py -3 "%HERE%ma.py" project init ^<mappa^> --title "Cím"
goto :fail

:fail
echo.
pause
if defined _OLDCP chcp %_OLDCP% >nul
endlocal
exit /b 1

:end
if defined _OLDCP chcp %_OLDCP% >nul
endlocal
exit /b 0
