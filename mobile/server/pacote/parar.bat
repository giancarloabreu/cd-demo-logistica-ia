@echo off
rem Encerra o servidor Separacao Redespacho desta maquina.
rem A logica esta em parar.ps1; este arquivo so chama o PowerShell.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0parar.ps1"
if errorlevel 1 pause
