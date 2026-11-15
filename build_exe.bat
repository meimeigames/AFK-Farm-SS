@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Сначала запустите install.bat
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install pyinstaller
".venv\Scripts\pyinstaller.exe" --noconfirm --clean --windowed --name AFKFarm main.py
if errorlevel 1 (
    echo Ошибка сборки.
    pause
    exit /b 1
)
echo.
echo Готово: dist\AFKFarm\AFKFarm.exe
echo Папку dist\AFKFarm можно переносить целиком. Настройки и журнал создаются рядом с exe.
pause
