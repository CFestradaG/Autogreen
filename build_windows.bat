@echo off
REM Genera dist\AutoGreen.exe (ejecutar en Windows con Python instalado)
python -m pip install --upgrade pip
python -m pip install pyinstaller pyserial requests
python -m PyInstaller --onefile --name AutoGreen --hidden-import serial.tools.list_ports_windows autogreen.py
echo.
echo Listo: dist\AutoGreen.exe
pause
