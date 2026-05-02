@echo off
setlocal

cd /d "%~dp0"

if not exist "logs" mkdir "logs"

set "LOGFILE=%~dp0logs\agent-discreto.log"
echo [%date% %time%] Iniciando agente...>> "%LOGFILE%"

if exist "%~dp0python-portatil\python.exe" (
  "%~dp0python-portatil\python.exe" "%~dp0agent.py" run >> "%LOGFILE%" 2>&1
  exit /b %errorlevel%
)

where python >nul 2>nul
if %errorlevel%==0 (
  python "%~dp0agent.py" run >> "%LOGFILE%" 2>&1
  exit /b %errorlevel%
)

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "%~dp0agent.py" run >> "%LOGFILE%" 2>&1
  exit /b %errorlevel%
)

echo [%date% %time%] ERRO: Python nao encontrado. Baixe o Python portatil ou instale Python.>> "%LOGFILE%"
exit /b 1
