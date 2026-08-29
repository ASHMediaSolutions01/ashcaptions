@echo off
rem Double-click installer for ASH Captions.
rem Thin wrapper: the real work is in install.ps1, sitting next to this file.
rem -ExecutionPolicy Bypass applies to this one process only -- it does not
rem change any system-wide PowerShell setting -- and no admin prompt is
rem needed because install.ps1 only ever writes to the current user's own
rem profile and to C:\AshCaptions.

setlocal
set "SCRIPT_DIR=%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%install.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Something went wrong during install ^(exit code %EXIT_CODE%^).
    echo Please screenshot this window and send it to Ghazi.
)

echo.
pause
endlocal
exit /b %EXIT_CODE%
