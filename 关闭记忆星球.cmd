@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\close-memory-planet.ps1"
if errorlevel 1 pause
