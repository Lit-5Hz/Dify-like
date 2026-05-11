@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart-backend.ps1" %*
