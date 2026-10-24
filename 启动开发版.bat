@echo off
cd /d "%~dp0"
set "DSHPY=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\pythonw.exe"
if exist "%DSHPY%" (
  start "" "%DSHPY%" "app.py"
  exit /b 0
)
where pythonw >nul 2>nul
if not errorlevel 1 (
  start "" pythonw "app.py"
  exit /b 0
)
echo Python was not found. Use KuaiFillVault.exe instead.
pause
