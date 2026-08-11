@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "JOBSLAYER_ROOT=%~dp0"
set "JOBSLAYER_LAUNCHER=%JOBSLAYER_ROOT%jobslayer"

if not defined JOBSLAYER_PYTHON goto discover_python
if exist "%JOBSLAYER_PYTHON%" goto run_configured_python
>&2 echo configured JobSlayer Python does not exist: %JOBSLAYER_PYTHON%
set "JOBSLAYER_EXIT=1"
goto finish

:run_configured_python
"%JOBSLAYER_PYTHON%" "%JOBSLAYER_LAUNCHER%" %*
set "JOBSLAYER_EXIT=!ERRORLEVEL!"
goto finish

:discover_python
if exist "%JOBSLAYER_ROOT%.venv\Scripts\python.exe" goto run_venv_python
where py.exe >nul 2>nul
if not errorlevel 1 goto run_py_launcher
where python.exe >nul 2>nul
if not errorlevel 1 goto run_path_python
>&2 echo JobSlayer requires Python 3.11 or newer; create .venv or set JOBSLAYER_PYTHON.
set "JOBSLAYER_EXIT=127"
goto finish

:run_venv_python
"%JOBSLAYER_ROOT%.venv\Scripts\python.exe" "%JOBSLAYER_LAUNCHER%" %*
set "JOBSLAYER_EXIT=!ERRORLEVEL!"
goto finish

:run_py_launcher
py.exe -3 "%JOBSLAYER_LAUNCHER%" %*
set "JOBSLAYER_EXIT=!ERRORLEVEL!"
goto finish

:run_path_python
python.exe "%JOBSLAYER_LAUNCHER%" %*
set "JOBSLAYER_EXIT=!ERRORLEVEL!"

:finish
endlocal & exit /b %JOBSLAYER_EXIT%
