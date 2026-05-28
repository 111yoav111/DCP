@echo off
call venv\Scripts\activate.bat

start cmd /k "venv\Scripts\activate && python src/master/main_master.py"

start cmd /k "venv\Scripts\activate && python src/worker/main_worker.py"
:: To add more workers, duplicate the line above