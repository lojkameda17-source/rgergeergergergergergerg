@echo off
py -3 launcher.py
if %errorlevel% equ 0 goto :eof
python launcher.py
if %errorlevel% equ 0 goto :eof
python3 launcher.py
if %errorlevel% equ 0 goto :eof
echo.
echo [ERROR] Python not found. Get it from https://python.org
echo During install check: Add Python to PATH
echo.
pause
