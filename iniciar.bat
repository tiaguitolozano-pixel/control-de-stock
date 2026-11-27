@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Control de stock

rem Busca Python: primero "py" (instalador de python.org), si no "python".
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo No se encontro Python. Instalalo desde https://www.python.org/downloads/
  echo IMPORTANTE: en el instalador marca la casilla "Add python.exe to PATH".
  pause
  exit /b 1
)

rem No hace falta internet: los componentes vienen en la carpeta "vendor".
%PY% iniciar.py
if errorlevel 1 (
  echo.
  echo El programa se cerro con un error. Saca una foto de esta ventana y mandala.
)
pause
