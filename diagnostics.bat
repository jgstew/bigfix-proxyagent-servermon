@echo off
rem diagnostics.bat - check that the ExecutablePath in settings.json can
rem actually launch servermon and, if it cannot, show which part is broken.
rem
rem When the interpreter is missing the plugin never starts, so
rem Logs\servermon.log is never written. This script needs no Python - it is
rem what you run when Python is the problem.
rem
rem Usage, from a Command Prompt in the plugin folder:
rem   diagnostics.bat           check as the current user
rem   diagnostics.bat /system   also re-run every check as LocalSystem, the
rem                             account BESProxyAgent runs plugins as
rem                             (needs an elevated "Run as administrator" prompt)
rem
rem Save the output to share it:  diagnostics.bat /system > diagnostics.txt 2>&1
rem
rem Read-only apart from temp files, except that /system briefly creates and
rem deletes a scheduled task, and the final check runs the plugin with
rem --validate (which only reads servermon.toml and writes its usual log).
rem
rem Exit code: number of FAIL results (0 = ExecutablePath works).

setlocal EnableExtensions EnableDelayedExpansion

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "SETTINGS=%ROOT%\settings.json"
set "FAILS=0"
set "WARNS=0"
set "ALSO_SYSTEM="
set "NESTED="
if /i "%~1"=="/system" set "ALSO_SYSTEM=1"
if /i "%~1"=="/nested" set "NESTED=1"
if not defined TEMP set "TEMP=%SystemRoot%\Temp"
set "DIAG_OUT=%TEMP%\servermon-diag-%RANDOM%%RANDOM%.txt"
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "PSRUN="%PS%" -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command"

rem Detect LocalSystem by SID - the account name is localized.
set "WHO=unknown"
set "SID="
for /f "tokens=1,2 delims=," %%U in ('whoami /user /fo csv /nh 2^>nul') do (
  set "WHO=%%~U"
  set "SID=%%~V"
)
set "IS_SYSTEM="
if "!SID!"=="S-1-5-18" set "IS_SYSTEM=1"
set "ELEVATED=no"
net session >nul 2>&1 && set "ELEVATED=yes"

echo ==== servermon ExecutablePath diagnostics ====
echo Date:          %DATE% %TIME%
echo Computer:      %COMPUTERNAME%
echo Running as:    !WHO!  (elevated: !ELEVATED!)
echo Plugin folder: !ROOT!
if not defined IS_SYSTEM (
  echo NOTE: the BESProxyAgent service runs the plugin as LocalSystem, which does
  echo       not see per-user Python installs or your user PATH. A PASS here does
  echo       not prove the service can run it - use "diagnostics.bat /system".
)

rem ---------------------------------------------------------------------------
echo.
echo [1] settings.json
if not exist "!SETTINGS!" (
  call :fail "settings.json not found: !SETTINGS!"
  goto :summary
)
call :pass "found !SETTINGS!"
if not exist "%PS%" (
  call :fail "PowerShell not found at %PS% - needed to read settings.json"
  call :hint "open settings.json and check ExecutablePath by hand"
  goto :summary
)
%PSRUN% "$ErrorActionPreference='Stop'; try { $s = Get-Content -Raw -LiteralPath $env:SETTINGS | ConvertFrom-Json } catch { Write-Output ('settings.json is not valid JSON: ' + $_.Exception.Message); exit 2 }; if (-not $s.ExecutablePath) { Write-Output 'settings.json has no ExecutablePath value'; exit 3 }; [IO.File]::WriteAllText($env:DIAG_OUT, [string]$s.ExecutablePath); exit 0"
if errorlevel 1 (
  call :fail "could not read ExecutablePath from settings.json (see message above)"
  call :hint "in JSON every backslash in a path must be doubled, and inner quotes escaped with a backslash"
  goto :summary
)
set "EXEC="
set /p EXEC=<"%DIAG_OUT%"
del "%DIAG_OUT%" >nul 2>&1
call :pass "ExecutablePath is valid JSON"
echo          ExecutablePath: !EXEC!

rem Split into: interpreter, interpreter args, script (first *.py), script args.
set "EXE="
set "IARGS="
set "SCRIPT="
set "SARGS="
for %%T in (!EXEC!) do (
  if not defined EXE (
    set "EXE=%%~T"
  ) else if not defined SCRIPT (
    if /i "%%~xT"==".py" (set "SCRIPT=%%~T") else set "IARGS=!IARGS! %%T"
  ) else set "SARGS=!SARGS! %%T"
)
echo          interpreter:    !EXE!
echo          interp. args:  !IARGS!
echo          script:         !SCRIPT!
if /i "!EXE:~-3!"==".py" (
  call :fail "ExecutablePath starts with the .py script, not an interpreter"
  call :hint "services do not use .py file associations - start it with py -3 or a full python.exe path"
  goto :summary
)
if not defined SCRIPT call :warn "no .py script found in ExecutablePath - script checks skipped"

rem ---------------------------------------------------------------------------
echo.
echo [2] Interpreter "!EXE!"
set "EXEFULL="
rem (Substring tests, not echo piped to find: a pipe runs in a child cmd that
rem does not expand !vars!.)
if "!EXE:~1,1!"==":" goto :exe_has_path
if not "!EXE:\=!"=="!EXE!" goto :exe_has_path

rem A bare name: the service resolves it like CreateProcess does as
rem LocalSystem - System32, the Windows folder, then the MACHINE PATH.
set "DIAG_NAME=!EXE!"
%PSRUN% "$n = $env:DIAG_NAME; if (-not [IO.Path]::HasExtension($n)) { $n += '.exe' }; $dirs = @([Environment]::SystemDirectory, $env:SystemRoot) + ([Environment]::GetEnvironmentVariable('Path', 'Machine') -split ';'); foreach ($d in $dirs) { try { if ($d.Trim()) { $p = Join-Path ([Environment]::ExpandEnvironmentVariables($d.Trim())) $n; if (Test-Path -LiteralPath $p -PathType Leaf) { [IO.File]::WriteAllText($env:DIAG_OUT, $p); exit 0 } } } catch {} }; exit 1"
if not errorlevel 1 (
  set /p EXEFULL=<"%DIAG_OUT%"
  del "%DIAG_OUT%" >nul 2>&1
)
set "USERHIT="
for /f "delims=" %%W in ('where "!EXE!" 2^>nul') do if not defined USERHIT set "USERHIT=%%W"
if defined EXEFULL (
  call :pass "found via the system search path: !EXEFULL!"
  if defined USERHIT if /i not "!USERHIT!"=="!EXEFULL!" echo          your own PATH finds a different one first: !USERHIT!
  goto :exe_found
)
if defined USERHIT (
  call :fail "!EXE! is only on YOUR user PATH (!USERHIT!) - the service will not find it"
  call :hint "put its folder on the machine PATH and reboot, or use its full path in ExecutablePath"
  goto :exe_missing
)
call :fail "!EXE! not found in System32, the Windows folder, or the machine PATH"
if /i "!EXE!"=="py" call :hint "the py launcher is installed by the python.org installer with 'Install launcher for all users'"
if /i "!EXE!"=="py.exe" call :hint "the py launcher is installed by the python.org installer with 'Install launcher for all users'"
call :hint "services read PATH at boot: after changing the machine PATH, reboot"
goto :exe_missing

:exe_has_path
set "EXEFULL=!EXE!"
if not exist "!EXEFULL!" if exist "!EXEFULL!.exe" set "EXEFULL=!EXEFULL!.exe"
if exist "!EXEFULL!" (
  call :pass "file exists: !EXEFULL!"
  goto :exe_found
)
call :fail "interpreter not found at !EXE!"
call :hint "it was probably removed or upgraded - a new Python version installs to a new folder"
set "EXEFULL="
goto :exe_missing

:exe_found
if not "!EXEFULL:\WindowsApps\=!"=="!EXEFULL!" (
  call :fail "this is a Microsoft Store app alias - services cannot run it"
  call :hint "install Python from python.org for all users"
)
if not "!EXEFULL:\Users\=!"=="!EXEFULL!" (
  call :warn "interpreter lives in a user profile - removed if that user's Python is upgraded or uninstalled"
  call :hint "prefer an all-users install (C:\Program Files\Python3xx) or the py launcher"
)

:exe_missing
rem ---------------------------------------------------------------------------
echo.
echo [3] Python installs on this machine
%PSRUN% "$p = @(); foreach ($b in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:SystemDrive)) { if ($b) { $p += Get-ChildItem -Path ($b + '\Python3*\python.exe') -ErrorAction SilentlyContinue } }; $p += Get-ChildItem -Path ($env:SystemDrive + '\Users\*\AppData\Local\Programs\Python\Python3*\python.exe') -ErrorAction SilentlyContinue; $r = @($p | ForEach-Object { $_.FullName }); foreach ($k in @('HKLM:\SOFTWARE\Python\PythonCore', 'HKLM:\SOFTWARE\WOW6432Node\Python\PythonCore')) { Get-ChildItem $k -ErrorAction SilentlyContinue | ForEach-Object { $e = (Get-ItemProperty ($_.PSPath + '\InstallPath') -ErrorAction SilentlyContinue).ExecutablePath; if ($e -and (Test-Path -LiteralPath $e)) { $r += $e } } }; $r = @($r | Sort-Object -Unique); Set-Content -LiteralPath $env:DIAG_OUT -Value $r; exit $r.Count"
if errorlevel 1 (
  for /f "usebackq delims=" %%L in ("%DIAG_OUT%") do (
    set "LINE=%%L"
    if not "!LINE:\Users\=!"=="!LINE!" (
      echo          %%L   [per-user: invisible to py run as LocalSystem]
    ) else (
      echo          %%L
    )
  )
) else (
  call :warn "no python.exe found in the usual all-users or per-user locations"
)
del "%DIAG_OUT%" >nul 2>&1
if not defined EXEFULL goto :files
for %%N in ("!EXEFULL!") do set "EXENAME=%%~nN"
if /i not "!EXENAME!"=="py" goto :run_python
echo          what this py launcher sees (as !WHO!):
"%EXEFULL%" -0p > "%DIAG_OUT%" 2>&1
for /f "usebackq delims=" %%L in ("%DIAG_OUT%") do echo            %%L
find /i "\Users\" "%DIAG_OUT%" >nul && call :warn "py lists per-user installs - LocalSystem's py will not see those"
del "%DIAG_OUT%" >nul 2>&1

:run_python
rem ---------------------------------------------------------------------------
echo.
echo [4] Run the interpreter:!IARGS! (needs Python 3.11+)
"%EXEFULL%"%IARGS% -c "import sys; print(sys.executable); print('.'.join(map(str, sys.version_info[:3]))); sys.exit(0 if sys.version_info >= (3, 11) else 3)" > "%DIAG_OUT%" 2>&1
set "RC=%ERRORLEVEL%"
set "PYEXE="
set "PYVER="
for /f "usebackq delims=" %%L in ("%DIAG_OUT%") do (
  if not defined PYEXE (set "PYEXE=%%L") else if not defined PYVER set "PYVER=%%L"
)
if "!RC!"=="0" (
  call :pass "Python !PYVER! runs: !PYEXE!"
  if not "!PYEXE:\Users\=!"=="!PYEXE!" call :warn "the interpreter actually used is a per-user install - LocalSystem may not get the same one"
) else if "!RC!"=="3" (
  call :fail "Python !PYVER! is too old - servermon needs 3.11+ - !PYEXE!"
) else (
  call :fail "the interpreter did not run, exit code !RC! - its output:"
  for /f "usebackq delims=" %%L in ("%DIAG_OUT%") do echo            %%L
  if /i "!EXENAME!"=="py" call :hint "py with no usable runtime: install Python 3.11+ for all users"
)
del "%DIAG_OUT%" >nul 2>&1
if not "!RC!"=="0" goto :files

"%EXEFULL%"%IARGS% -c "import ssl, tomllib; print(ssl.OPENSSL_VERSION)" > "%DIAG_OUT%" 2>&1
if errorlevel 1 (
  call :fail "required standard library modules (ssl, tomllib) failed to import:"
  for /f "usebackq delims=" %%L in ("%DIAG_OUT%") do echo            %%L
  call :hint "the Python install is broken - repair or reinstall it"
) else (
  set /p SSLVER=<"%DIAG_OUT%"
  call :pass "ssl and tomllib import (!SSLVER!)"
)
del "%DIAG_OUT%" >nul 2>&1

"%EXEFULL%"%IARGS% -s -c "import certifi; print(certifi.where())" > "%DIAG_OUT%" 2>nul
if errorlevel 1 (
  echo   [INFO] certifi not installed for all users ^(optional - README "TLS trust store"^)
) else (
  set /p CERTIFI=<"%DIAG_OUT%"
  echo   [INFO] certifi: !CERTIFI!
)
del "%DIAG_OUT%" >nul 2>&1

:files
rem ---------------------------------------------------------------------------
echo.
echo [5] Plugin files
if defined SCRIPT (
  if exist "!SCRIPT!" (
    call :pass "script exists: !SCRIPT!"
  ) else (
    call :fail "script not found: !SCRIPT!"
    call :hint "fix the script path in ExecutablePath - it must be absolute"
  )
  if /i not "!SCRIPT!"=="!ROOT!\plugin\servermon.py" call :warn "ExecutablePath runs a script outside this folder (expected !ROOT!\plugin\servermon.py)"
)
if exist "!ROOT!\vendor\bigfix_proxyagent-*.whl" (
  call :pass "vendored SDK wheel present"
) else (
  call :fail "vendor\bigfix_proxyagent-*.whl missing - the plugin folder is incomplete"
)
if exist "!ROOT!\servermon.toml" (
  call :pass "servermon.toml present"
) else (
  call :fail "servermon.toml missing"
)

rem ---------------------------------------------------------------------------
echo.
echo [6] End to end: ExecutablePath --configOptions "" --validate
if not defined EXEFULL (
  echo   [SKIP] interpreter not found
  goto :service
)
if not "!RC!"=="0" (
  echo   [SKIP] interpreter does not run
  goto :service
)
rem Run the exact command line, from System32 as a service would.
pushd "%SystemRoot%\System32"
%EXEC% --configOptions "" --validate > "%DIAG_OUT%" 2>&1
set "RC=%ERRORLEVEL%"
popd
for /f "usebackq delims=" %%L in ("%DIAG_OUT%") do echo            %%L
del "%DIAG_OUT%" >nul 2>&1
if "!RC!"=="0" (
  call :pass "ExecutablePath launches servermon and the config validates"
) else (
  call :fail "the plugin exited with code !RC! - see its output above and Logs\servermon.log"
)

:service
rem ---------------------------------------------------------------------------
echo.
echo [7] Service and logs
set "SVCSTATE="
set "SVCUSER="
for /f "tokens=4" %%A in ('sc query BESProxyAgent 2^>nul ^| find "STATE"') do set "SVCSTATE=%%A"
for /f "tokens=1,* delims=:" %%A in ('sc qc BESProxyAgent 2^>nul ^| find "SERVICE_START_NAME"') do set "SVCUSER=%%B"
if defined SVCSTATE (
  echo   [INFO] BESProxyAgent service: !SVCSTATE!, runs as!SVCUSER!
  if /i not "!SVCSTATE!"=="RUNNING" call :warn "BESProxyAgent is not running"
) else (
  echo   [INFO] BESProxyAgent service not found on this machine
)
if exist "!ROOT!\Logs\servermon.log" (
  for %%F in ("!ROOT!\Logs\servermon.log") do echo   [INFO] plugin log last written %%~tF: %%~fF
) else (
  echo   [INFO] no Logs\servermon.log - consistent with the plugin never starting
)
for %%F in ("!ROOT!\..\..\__Logs") do if exist "%%~fF" echo   [INFO] Proxy Agent's own log ^(launch errors^): %%~fF

:summary
rem ---------------------------------------------------------------------------
echo.
echo ==== Result as !WHO!: !FAILS! failed, !WARNS! warning(s) ====
if "!FAILS!"=="0" (
  echo ExecutablePath works for this account.
) else (
  echo Fix the FAIL lines above, restart BESProxyAgent, then run this again.
)
del "%DIAG_OUT%" >nul 2>&1
if defined ALSO_SYSTEM goto :as_system
if not defined NESTED if not defined IS_SYSTEM (
  echo To check as the service account: diagnostics.bat /system  ^(elevated prompt^)
)
endlocal & exit /b %FAILS%

rem ---------------------------------------------------------------------------
rem Re-run this script as LocalSystem through a one-shot scheduled task, since
rem that is what the service sees; cmd has no built-in "run as SYSTEM".
:as_system
echo.
echo ==== Re-running as LocalSystem ====
if not "!ELEVATED!"=="yes" (
  echo /system needs an elevated Command Prompt ^(Run as administrator^).
  set /a FAILS+=1
  goto :as_system_end
)
set "SYS_DIR=!ROOT!\Logs"
if not exist "!SYS_DIR!" mkdir "!SYS_DIR!"
set "SYS_OUT=!SYS_DIR!\diagnostics-SYSTEM.txt"
set "SYS_CMD=!SYS_DIR!\diagnostics-SYSTEM.cmd"
set "SYS_DONE=!SYS_DIR!\diagnostics-SYSTEM.done"
set "TASK=servermon-diagnostics"
del "%SYS_OUT%" "%SYS_DONE%" >nul 2>&1
> "%SYS_CMD%" echo @call "%~f0" /nested ^> "%SYS_OUT%" 2^>^&1
>> "%SYS_CMD%" echo @^> "%SYS_DONE%" echo %%ERRORLEVEL%%
schtasks /create /tn "%TASK%" /tr "\"%SYS_CMD%\"" /sc once /st 00:00 /ru SYSTEM /rl HIGHEST /f >nul 2>&1
if errorlevel 1 (
  echo could not create the scheduled task "%TASK%"
  set /a FAILS+=1
  goto :as_system_cleanup
)
schtasks /run /tn "%TASK%" >nul 2>&1
set /a WAITED=0
:as_system_wait
if exist "%SYS_DONE%" goto :as_system_done
if !WAITED! geq 180 (
  echo timed out after 180s waiting for the LocalSystem run
  set /a FAILS+=1
  goto :as_system_cleanup
)
>nul ping -n 3 127.0.0.1
set /a WAITED+=2
goto :as_system_wait
:as_system_done
rem Give the task a moment to release the output file before reading it.
>nul ping -n 2 127.0.0.1
type "%SYS_OUT%"
set "SYS_RC=0"
set /p SYS_RC=<"%SYS_DONE%"
set /a FAILS+=SYS_RC
echo (LocalSystem output saved to %SYS_OUT%)
:as_system_cleanup
schtasks /delete /tn "%TASK%" /f >nul 2>&1
del "%SYS_CMD%" "%SYS_DONE%" >nul 2>&1
:as_system_end
endlocal & exit /b %FAILS%

rem ---------------------------------------------------------------------------
:pass
set "MSG=%~1"
echo   [PASS] !MSG!
exit /b 0

:fail
set "MSG=%~1"
echo   [FAIL] !MSG!
set /a FAILS+=1
exit /b 0

:warn
set "MSG=%~1"
echo   [WARN] !MSG!
set /a WARNS+=1
exit /b 0

:hint
set "MSG=%~1"
echo          fix: !MSG!
exit /b 0
