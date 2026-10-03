@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo BioTrack Studio Conda Setup
echo Folder: %CD%
echo.

set "CONDA_CMD="

if defined CONDA_EXE (
    if exist "%CONDA_EXE%" set "CONDA_CMD=%CONDA_EXE%"
)

if not defined CONDA_CMD (
    for %%C in (conda.exe conda.bat conda) do (
        for /f "delims=" %%P in ('where %%C 2^>nul') do (
            set "CONDA_CMD=%%P"
            goto :found_conda
        )
    )
)

if not defined CONDA_CMD (
    if exist "%USERPROFILE%\miniforge3\Scripts\conda.exe" set "CONDA_CMD=%USERPROFILE%\miniforge3\Scripts\conda.exe"
)
if not defined CONDA_CMD (
    if exist "%USERPROFILE%\miniconda3\Scripts\conda.exe" set "CONDA_CMD=%USERPROFILE%\miniconda3\Scripts\conda.exe"
)
if not defined CONDA_CMD (
    if exist "%USERPROFILE%\anaconda3\Scripts\conda.exe" set "CONDA_CMD=%USERPROFILE%\anaconda3\Scripts\conda.exe"
)
if not defined CONDA_CMD (
    if exist "%ProgramData%\miniconda3\Scripts\conda.exe" set "CONDA_CMD=%ProgramData%\miniconda3\Scripts\conda.exe"
)
if not defined CONDA_CMD (
    if exist "%ProgramData%\anaconda3\Scripts\conda.exe" set "CONDA_CMD=%ProgramData%\anaconda3\Scripts\conda.exe"
)

:found_conda
if defined CONDA_CMD (
    call "%CONDA_CMD%" --version >nul 2>nul
    if not errorlevel 1 (
        echo Conda is already available:
        echo %CONDA_CMD%
        echo.
        echo Run the BioTrack Studio launcher:
        echo winstart.bat
        pause
        exit /b 0
    )
)

if /i "%PROCESSOR_ARCHITECTURE%"=="ARM64" (
    echo Windows ARM64 automatic Miniforge setup is not supported by this script.
    echo Please install Miniforge or Miniconda manually, then run winstart.bat.
    echo https://conda-forge.org/download/
    pause
    exit /b 1
)

if defined BIOTRACK_CONDA_PREFIX (
    set "PREFIX=%BIOTRACK_CONDA_PREFIX%"
) else (
    set "PREFIX=%USERPROFILE%\miniforge3"
)

if exist "%PREFIX%" (
    if not exist "%PREFIX%\Scripts\conda.exe" (
        echo Install target already exists but is not a conda installation:
        echo %PREFIX%
        echo Remove that folder manually or set BIOTRACK_CONDA_PREFIX to another user-writable path.
        pause
        exit /b 1
    )
)

set "URL=https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Windows-x86_64.exe"
set "INSTALLER=%TEMP%\Miniforge3-Windows-x86_64.exe"
set "LOCAL_INSTALLER=%CD%\installers\Miniforge3-Windows-x86_64.exe"
set "DELETE_INSTALLER=1"

if exist "%LOCAL_INSTALLER%" (
    echo Using bundled Miniforge installer:
    echo %LOCAL_INSTALLER%
    set "INSTALLER=%LOCAL_INSTALLER%"
    set "DELETE_INSTALLER=0"
) else (
    echo Bundled Miniforge installer was not found:
    echo %LOCAL_INSTALLER%
    echo.
    echo Downloading Miniforge:
    echo %URL%
    echo.

    powershell -NoProfile -ExecutionPolicy Bypass -Command "try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%URL%' -OutFile '%INSTALLER%' -UseBasicParsing } catch { Write-Error $_; exit 1 }"
    if errorlevel 1 (
        echo.
        echo Could not download Miniforge.
        echo For offline setup, copy Miniforge3-Windows-x86_64.exe into the installers folder and rerun this script.
        pause
        exit /b 1
    )
)

if "%DELETE_INSTALLER%"=="0" if exist "%CD%\INSTALLER_CHECKSUMS.txt" (
    powershell -NoProfile -Command "$line = Get-Content -LiteralPath '%CD%\INSTALLER_CHECKSUMS.txt' | Where-Object { $_ -match 'Miniforge3-Windows-x86_64\.exe\s*$' } | Select-Object -First 1; if (-not $line) { Write-Error 'Installer checksum entry was not found.'; exit 1 }; $expected = ($line -split '\s+')[0]; $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath '%INSTALLER%').Hash; if ($expected -ne $actual) { Write-Error ('Bundled installer checksum failed. Expected ' + $expected + ', got ' + $actual); exit 1 }"
    if errorlevel 1 (
        pause
        exit /b 1
    )
    echo Bundled installer checksum verified.
)

echo.
echo Installing Miniforge to:
echo %PREFIX%
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$prefix = '%PREFIX%'; $installer = '%INSTALLER%'; $args = @('/S', '/InstallationType=JustMe', '/RegisterPython=0', '/AddToPath=0', ('/D=' + $prefix)); $p = Start-Process -FilePath $installer -ArgumentList $args -Wait -PassThru; exit $p.ExitCode"
set "INSTALL_EXIT=%ERRORLEVEL%"
if "%DELETE_INSTALLER%"=="1" del "%INSTALLER%" >nul 2>nul

if not "%INSTALL_EXIT%"=="0" (
    echo Miniforge installer failed. Exit code: %INSTALL_EXIT%
    pause
    exit /b 1
)

if not exist "%PREFIX%\Scripts\conda.exe" (
    echo Miniforge installed, but conda.exe was not found:
    echo %PREFIX%\Scripts\conda.exe
    pause
    exit /b 1
)

call "%PREFIX%\Scripts\conda.exe" --version >nul 2>nul
if errorlevel 1 (
    echo Miniforge installed, but conda could not run:
    echo %PREFIX%\Scripts\conda.exe
    pause
    exit /b 1
)

echo.
echo Conda setup finished:
echo %PREFIX%\Scripts\conda.exe
echo.
echo Run the BioTrack Studio launcher:
echo winstart.bat
pause
exit /b 0
