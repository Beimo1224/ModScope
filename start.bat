@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title ModScope - Minecraft MOD Conflict Finder
set "PYTHON_CMD="
where py >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD (
  where python >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
  echo.
  echo Python 3.11 or newer was not found.
  echo Install Python from https://www.python.org/downloads/ and enable "Add Python to PATH".
  echo.
  pause
  exit /b 1
)
%PYTHON_CMD% app.py %*
if errorlevel 1 (
  echo.
  echo ModScope stopped with an error. Press any key to close.
  pause >nul
)
