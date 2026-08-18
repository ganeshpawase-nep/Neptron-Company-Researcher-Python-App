
@echo off
if not exist .venv python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium
if not exist .env copy .env.example .env
.venv\Scripts\python.exe main.py
pause
