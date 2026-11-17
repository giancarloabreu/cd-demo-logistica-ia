@echo off
rem Sobe o servidor, ou abre a consulta se ja houver um no ar na rede.
rem A logica esta em iniciar.ps1; este arquivo so chama o PowerShell.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0iniciar.ps1" %*
if errorlevel 1 pause
