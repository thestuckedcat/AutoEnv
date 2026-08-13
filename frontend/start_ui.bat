@echo off
setlocal
cd /d "%~dp0.."
python frontend\server.py
if errorlevel 1 pause
