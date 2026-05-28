@echo off
call venv\Scripts\activate.bat

start cmd /k "venv\Scripts\activate && python master/main.py"

start cmd /k "venv\Scripts\activate && python worker/main.py"
:: To add more workers, duplicate the line above