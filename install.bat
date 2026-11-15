@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Установка AFK Farm ===
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m venv .venv
) else (
    python -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
    echo Не найден Python 3.10+. Установите его с https://www.python.org/downloads/ ^(галочка "Add python.exe to PATH"^).
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Ошибка установки зависимостей.
    pause
    exit /b 1
)
echo.
echo Готово. Запускайте run.bat
pause
