@echo off
REM Argos setup — fresh-clone bootstrap (Windows).
REM Installs the deps if they're missing, then runs the prerequisite checker.
REM Prefers the `py` launcher, falls back to `python` on PATH. Run from anywhere:
REM %~dp0 is this script's own folder, so bootstrap.py is found next to it.
where py >nul 2>nul && (py "%~dp0bootstrap.py" %* & goto :eof)
python "%~dp0bootstrap.py" %*
