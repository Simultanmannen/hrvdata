@echo off
chcp 65001 >nul
title HRV-data
cd /d "%~dp0"

rem Allt som laddas ner hamnar i undermappen .verktyg - inget installeras i systemet.
set "TOOLS=%~dp0.verktyg"
set "PKG=%TOOLS%\paket"
set "UV_INSTALL_DIR=%TOOLS%\uv"
set "UV_NO_MODIFY_PATH=1"
set "UV_PYTHON_INSTALL_DIR=%TOOLS%\python"
set "UV_CACHE_DIR=%TOOLS%\cache"
set "UV_PYTHON_PREFERENCE=only-managed"
set "UV=%UV_INSTALL_DIR%\uv.exe"
rem Låst Python-version. Senaste versionen kan blockeras på jobbdatorer: Defender-regeln som stoppar
rem nya, ovanliga program (förekomst/ålder) stoppade 3.12.16 dagen efter att den släpptes. Höj först
rem när en ny version har funnits några veckor och är provkörd på en sådan dator.
set "PYVER=3.12.15"

if exist "%UV%" goto python
echo.
echo  Första starten: laddar ner det som behövs. Det tar 1-3 minuter.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
if not exist "%UV%" goto fail_download

:python
"%UV%" python install %PYVER% --quiet
if errorlevel 1 goto fail_python
set "PY="
for /f "usebackq delims=" %%p in (`"%UV%" python find %PYVER%`) do set "PY=%%p"
if not defined PY goto fail_python
"%PY%" -c "pass" >nul 2>&1
if errorlevel 1 goto fail_python

rem Installera paket om de saknas eller om appen har uppdaterats
fc /b "requirements-lock.txt" "%TOOLS%\installerat.txt" >nul 2>&1
if not errorlevel 1 goto run
echo  Installerar paket ...
if exist "%PKG%" rmdir /s /q "%PKG%"
"%UV%" pip install --python "%PY%" --target "%PKG%" -r requirements-lock.txt --quiet
if errorlevel 1 goto fail_download
copy /y "requirements-lock.txt" "%TOOLS%\installerat.txt" >nul

:run
set "PYTHONPATH=%PKG%"
set "PYTHONNOUSERSITE=1"
echo  Startar HRV-data ...
rem Allt på en rad: filen kan skrivas om av en uppdatering medan appen körs
"%PY%" starta.py & if errorlevel 1 (echo. & echo  Något gick fel. Ta en skärmbild av det här fönstret och skicka till den som gav dig appen. & pause & exit /b 1) else exit /b 0

:fail_download
echo.
echo  Kunde inte ladda ner det som behövs. Kontrollera internetanslutningen och försök igen.
pause
exit /b 1

:fail_python
echo.
echo  Python kunde inte startas. Om det här är en jobbdator kan IT:s säkerhetsregler stoppa
echo  program som laddats ner nyligen. Ta en skärmbild av det här fönstret och skicka till den
echo  som gav dig appen.
pause
exit /b 1
