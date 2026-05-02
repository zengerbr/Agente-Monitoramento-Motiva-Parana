@echo off
setlocal
cd /d "%~dp0"

set "PORTABLE_PYTHON=%~dp0python-portatil\python.exe"

if exist "%PORTABLE_PYTHON%" (
  echo Usando Python portatil: %PORTABLE_PYTHON%
  "%PORTABLE_PYTHON%" "%~dp0agent.py" run
  goto :end
)

where python >nul 2>nul
if %errorlevel%==0 (
  echo Usando Python instalado no Windows.
  python "%~dp0agent.py" run
  goto :end
)

where py >nul 2>nul
if %errorlevel%==0 (
  echo Usando Python Launcher do Windows.
  py -3 "%~dp0agent.py" run
  goto :end
)

echo.
echo Nao foi possivel encontrar o Python.
echo.
echo Opcoes:
echo 1. Instale o Python 3.10 ou superior no Windows; ou
echo 2. Coloque o Python portatil em: %~dp0python-portatil
echo.
echo O arquivo esperado para o modo portatil e:
echo %~dp0python-portatil\python.exe
echo.
pause

:end
endlocal
