@echo off
REM 打包成單一 exe：dist\AutoDoctorChecker.exe
REM 需求：pip install -r requirements.txt pyinstaller
REM （selenium-manager 由 PyInstaller 內建的 selenium hook 自動打包）
cd /d "%~dp0"

REM conda 環境：讓 PyInstaller 抓到「這個」Python 的 Tcl/Tk DLL，
REM 避免抓到 PATH 上其他 conda 環境的不同版本（執行時會出現 Tcl version conflict）
for /f "delims=" %%i in ('python -c "import sys; print(sys.prefix)"') do set PY_PREFIX=%%i
if exist "%PY_PREFIX%\Library\bin" set "PATH=%PY_PREFIX%\Library\bin;%PATH%"

python -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name AutoDoctorChecker ^
    --add-data "alarm.mp3;." ^
    --hidden-import plyer.platforms.win.notification ^
    DoctorChecker.py
