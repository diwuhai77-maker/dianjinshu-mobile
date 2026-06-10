@echo off
cd /d "%~dp0"
set PYTHON_EXE=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe
if exist "%PYTHON_EXE%" (
  "%PYTHON_EXE%" app.py
) else (
  py -3 app.py
)
pause
