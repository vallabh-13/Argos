@echo off
REM Argos setup — thin wrapper for `python -m argos setup` (Windows).
REM Prefers the `py` launcher, falls back to `python` on PATH.
where py >nul 2>nul && (py -m argos setup %* & goto :eof)
python -m argos setup %*
