@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "JOBSLAYER_INIT_ROOT=%~dp0"
set "JOBSLAYER_INIT_SCRIPT=%JOBSLAYER_INIT_ROOT%scripts\bootstrap.py"

if not defined JOBSLAYER_BOOTSTRAP_PYTHON goto discover_python
if exist "%JOBSLAYER_BOOTSTRAP_PYTHON%" goto run_configured_python
>&2 echo configured bootstrap Python does not exist: %JOBSLAYER_BOOTSTRAP_PYTHON%
set "JOBSLAYER_INIT_EXIT=127"
goto finish

:run_configured_python
"%JOBSLAYER_BOOTSTRAP_PYTHON%" "%JOBSLAYER_INIT_SCRIPT%" %*
set "JOBSLAYER_INIT_EXIT=!ERRORLEVEL!"
goto finish

:discover_python
if exist "%JOBSLAYER_INIT_ROOT%.venv\Scripts\python.exe" goto run_venv_python
where py.exe >nul 2>nul
if errorlevel 1 goto discover_path_python
py.exe -3.11 -c "import sys" >nul 2>nul
if not errorlevel 1 goto run_py311
py.exe -3 -c "import sys" >nul 2>nul
if not errorlevel 1 goto run_py3

:discover_path_python
where python.exe >nul 2>nul
if not errorlevel 1 goto run_path_python
>&2 echo JobSlayer initialization requires Python 3.11 or newer.
>&2 echo Install Python, or set JOBSLAYER_BOOTSTRAP_PYTHON to an existing interpreter.
set "JOBSLAYER_INIT_EXIT=127"
goto finish

:run_venv_python
"%JOBSLAYER_INIT_ROOT%.venv\Scripts\python.exe" "%JOBSLAYER_INIT_SCRIPT%" %*
set "JOBSLAYER_INIT_EXIT=!ERRORLEVEL!"
goto finish

:run_py311
py.exe -3.11 "%JOBSLAYER_INIT_SCRIPT%" %*
set "JOBSLAYER_INIT_EXIT=!ERRORLEVEL!"
goto finish

:run_py3
py.exe -3 "%JOBSLAYER_INIT_SCRIPT%" %*
set "JOBSLAYER_INIT_EXIT=!ERRORLEVEL!"
goto finish

:run_path_python
python.exe "%JOBSLAYER_INIT_SCRIPT%" %*
set "JOBSLAYER_INIT_EXIT=!ERRORLEVEL!"

:finish
endlocal & exit /b %JOBSLAYER_INIT_EXIT%
