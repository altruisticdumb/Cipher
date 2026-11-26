@echo off
rem Re-run inside a window that stays open, so any error stays readable.
if "%~1"=="child" goto :main
cmd /k ""%~f0" child"
exit /b
:main
rem Builds the dist\Cipher folder (Cipher.exe plus its files) with flet pack, then CipherSetup-<version>.exe with Inno Setup.
rem Run from the Cipher folder:  build_installer.bat
setlocal enabledelayedexpansion
cd /d "%~dp0"

if exist patch_flet_pack.py (
    py patch_flet_pack.py
    if errorlevel 1 goto :fail
) else (
    echo patch_flet_pack.py not found, so skipping the flet fix. If packing fails with
    echo "binary mode doesn't take an encoding argument", copy that file here and run again.
)

for /f "usebackq delims=" %%i in (`py -c "import sysconfig;print(sysconfig.get_path('scripts'))"`) do set "SCRIPTS=%%i"
if not exist "%SCRIPTS%\flet.exe" (
    echo Couldn't find flet.exe in %SCRIPTS%. Install it with:  py -m pip install "flet[all]"
    goto :fail
)

echo.
echo Packing Cipher as a folder ...
"%SCRIPTS%\flet.exe" pack app.py --name Cipher --product-name "Cipher" --icon installer\cipher.ico -D -y
if errorlevel 1 goto :fail
if not exist dist\Cipher\Cipher.exe (
    echo dist\Cipher\Cipher.exe wasn't created.
    goto :fail
)

set "ISCC="
rem any installed Inno Setup version (6, 7, ...), per-user or for all users; the newest found wins
rem the paths go through variables read with ! (not %) because "Program Files (x86)" has brackets
set "ROOT1=%LOCALAPPDATA%\Programs"
set "ROOT2=%ProgramFiles(x86)%"
set "ROOT3=%ProgramFiles%"
for %%R in (ROOT1 ROOT2 ROOT3) do (
    if defined %%R for /d %%D in ("!%%R!\Inno Setup*") do if exist "%%D\ISCC.exe" set "ISCC=%%D\ISCC.exe"
)
if "%ISCC%"=="" (
    echo.
    echo The Cipher folder is ready in dist\. To make the installer, install Inno Setup 6 or newer from
    echo https://jrsoftware.org/isdl.php  then run this file again.
    goto :fail
)

echo.
echo Building the installer ...
"%ISCC%" installer\cipher.iss
if errorlevel 1 goto :fail
echo.
echo Done. The installer is in installer\output\
pause
exit /b 0

:fail
echo.
echo The build stopped. See the message above.
pause
exit /b 1
