@echo off
rem Double-click uninstaller for ASH Captions.
rem Thin wrapper: the real work is in uninstall.ps1, sitting next to this file.
rem -ExecutionPolicy Bypass applies to this one process only -- it does not
rem change any system-wide PowerShell setting -- and no admin prompt is
rem needed because uninstall.ps1 only ever removes what install.ps1 put in
rem the current user's own profile. C:\AshCaptions (your captions) is kept
rem unless you run:  Uninstall-AshCaptions.bat -RemoveData

setlocal
set "SCRIPT_DIR=%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%uninstall.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Something went wrong during uninstall ^(exit code %EXIT_CODE%^).
    echo Please screenshot this window and send it to Ghazi.
)

echo.
pause
endlocal
exit /b %EXIT_CODE%
