@echo off
REM Use the external configuration loader to install and launch the GUI plugin.
python "%~dp0..\..\run.py" --launch-only
if errorlevel 1 pause
