@echo off
setlocal
cd /d "%~dp0"
set "PYTHONNOUSERSITE=1"

echo BioTrack Studio Launcher
echo Folder: %CD%
echo.
echo This window will show setup progress.
echo If something fails, send BioTrack_Studio_install_log.txt to support.
echo.

set "CONDA_CMD="
set "PYTHON_CMD="

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
    if exist "%USERPROFILE%\miniconda3\Scripts\conda.exe" set "CONDA_CMD=%USERPROFILE%\miniconda3\Scripts\conda.exe"
)

if not defined CONDA_CMD (
    if exist "%USERPROFILE%\anaconda3\Scripts\conda.exe" set "CONDA_CMD=%USERPROFILE%\anaconda3\Scripts\conda.exe"
)

if not defined CONDA_CMD (
    if exist "%USERPROFILE%\mambaforge\Scripts\conda.exe" set "CONDA_CMD=%USERPROFILE%\mambaforge\Scripts\conda.exe"
)

if not defined CONDA_CMD (
    if exist "%USERPROFILE%\miniforge3\Scripts\conda.exe" set "CONDA_CMD=%USERPROFILE%\miniforge3\Scripts\conda.exe"
)

if not defined CONDA_CMD (
    if exist "%ProgramData%\miniconda3\Scripts\conda.exe" set "CONDA_CMD=%ProgramData%\miniconda3\Scripts\conda.exe"
)

if not defined CONDA_CMD (
    if exist "%ProgramData%\anaconda3\Scripts\conda.exe" set "CONDA_CMD=%ProgramData%\anaconda3\Scripts\conda.exe"
)

:found_conda
if not defined CONDA_CMD (
    echo Could not find conda.
    echo Install Miniconda or Anaconda first, then run this file again.
    echo Miniconda download:
    echo https://docs.conda.io/en/latest/miniconda.html
    pause
    exit /b 1
)

for %%I in ("%CONDA_CMD%") do set "CONDA_DIR=%%~dpI"
if exist "%CONDA_DIR%..\Scripts\conda.exe" set "CONDA_CMD=%CONDA_DIR%..\Scripts\conda.exe"
for %%I in ("%CONDA_CMD%") do set "CONDA_DIR=%%~dpI"

call "%CONDA_CMD%" --version >nul 2>nul
if errorlevel 1 (
    echo Found conda, but it cannot run:
    echo %CONDA_CMD%
    pause
    exit /b 1
)

if defined CONDA_PYTHON_EXE (
    if exist "%CONDA_PYTHON_EXE%" set "PYTHON_CMD=%CONDA_PYTHON_EXE%"
)

if not defined PYTHON_CMD (
    if exist "%CONDA_DIR%python.exe" set "PYTHON_CMD=%CONDA_DIR%python.exe"
)

if not defined PYTHON_CMD (
    if exist "%CONDA_DIR%..\python.exe" set "PYTHON_CMD=%CONDA_DIR%..\python.exe"
)

if not defined PYTHON_CMD (
    for %%P in (python.exe py.exe python) do (
        for /f "delims=" %%Q in ('where %%P 2^>nul') do (
            set "PYTHON_CMD=%%Q"
            goto :found_python
        )
    )
)

:found_python
if not defined PYTHON_CMD (
    echo Could not find Python to run the launcher.
    echo Install Python or fix the conda installation, then run this file again.
    pause
    exit /b 1
)

call "%PYTHON_CMD%" -c "import pathlib, subprocess, sys" >nul 2>nul
if errorlevel 1 (
    echo Found Python, but it cannot run the launcher:
    echo %PYTHON_CMD%
    pause
    exit /b 1
)

if not defined CONDA_ENVS_PATH (
    set "CONDA_ENVS_PATH=%USERPROFILE%\.conda\envs"
    if not exist "%CONDA_ENVS_PATH%" mkdir "%CONDA_ENVS_PATH%" >nul 2>nul
)

if not defined CONDA_PKGS_DIRS (
    set "CONDA_PKGS_DIRS=%USERPROFILE%\.conda\pkgs"
    if not exist "%CONDA_PKGS_DIRS%" mkdir "%CONDA_PKGS_DIRS%" >nul 2>nul
)

set "CONDA_EXE=%CONDA_CMD%"

echo Using conda:
echo %CONDA_CMD%
echo Using Python:
echo %PYTHON_CMD%
echo Conda envs path:
echo %CONDA_ENVS_PATH%
echo Conda package cache:
echo %CONDA_PKGS_DIRS%
echo.
echo Starting setup. First setup can take a long time.
echo.

call "%PYTHON_CMD%" "%~dp0launch_biotrack_studio.py"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo BioTrack Studio launcher failed.
    echo Exit code: %EXIT_CODE%
    echo Please send BioTrack_Studio_install_log.txt to support.
    pause
    exit /b 1
)

echo.
echo BioTrack Studio launcher finished.
pause

endlocal
