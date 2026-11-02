#!/bin/bash
# Genera dist/AutoGreen (ejecutar en macOS con Python 3 instalado)
set -e
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install pyinstaller pyserial requests
python -m PyInstaller --onefile --name AutoGreen --hidden-import serial.tools.list_ports_osx autogreen.py
echo
echo "Listo: dist/AutoGreen   (ejecutar con ./dist/AutoGreen)"
