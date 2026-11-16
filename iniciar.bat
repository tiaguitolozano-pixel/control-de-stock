@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Control de stock

where python >nul 2>nul
if errorlevel 1 (
  echo No se encontro Python. Instalalo desde https://www.python.org/downloads/
  echo IMPORTANTE: en el instalador marca la casilla "Add python.exe to PATH".
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Primera vez: preparando el programa, puede tardar un minuto...
  python -m venv .venv
)
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo No se pudieron instalar los componentes. Revisa la conexion a internet.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" iniciar.py
pause
