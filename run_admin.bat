@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Запуск с правами администратора: нужен, если игра запущена от администратора
if not exist ".venv\Scripts\pythonw.exe" (
    echo Сначала запустите install.bat
    pause
    exit /b 1
)
powershell -NoProfile -Command "Start-Process -FilePath '%~dp0.venv\Scripts\pythonw.exe' -ArgumentList '\"%~dp0main.py\"' -WorkingDirectory '%~dp0' -Verb RunAs"
