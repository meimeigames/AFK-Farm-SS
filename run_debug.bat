@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Запуск с консолью: видны ошибки, если программа не стартует
".venv\Scripts\python.exe" main.py
pause
