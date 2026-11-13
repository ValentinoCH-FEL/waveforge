@echo off
REM Waveforge: doble clic para abrir. La primera vez prepara todo solo:
REM   1. crea el entorno de Python (.venv) en esta carpeta,
REM   2. instala las dependencias (y de nuevo cuando requirements.txt cambie),
REM   3. compila el motor de filtros en C++ si falta,
REM   4. ofrece instalar ffmpeg si no está.
setlocal
cd /d "%~dp0"
chcp 65001 >nul

REM ---- 1. Python y entorno ----
set "VPY=.venv\Scripts\python.exe"
if exist "%VPY%" goto :dependencias
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
    echo No se encontro Python. Instalalo desde https://www.python.org/downloads/
    echo o con: winget install Python.Python.3.14
    goto :error
)
echo Creando el entorno de Python en .venv ...
%PY% -m venv .venv || goto :error

:dependencias
REM ---- 2. Dependencias: solo si requirements.txt cambio desde la ultima instalacion ----
fc /b requirements.txt .venv\requirements.instalado >nul 2>nul && goto :motor
echo Instalando dependencias. La primera vez tarda varios minutos (PyTorch pesa ~250 MB)...
"%VPY%" -m pip install --disable-pip-version-check -q --upgrade pip || goto :error
"%VPY%" -m pip install --disable-pip-version-check -r requirements.txt || goto :error
"%VPY%" -m pip install --disable-pip-version-check -q --no-deps audio-separator || goto :error
copy /y requirements.txt .venv\requirements.instalado >nul

:motor
REM ---- 3. Motor nativo ----
if exist native\filtro.dll goto :ffmpeg
where g++ >nul 2>nul || (
    echo.
    echo Aviso: falta g++ para compilar el motor de filtros. La app abre, pero sin efectos.
    echo Instala MSYS2 ^(https://www.msys2.org/^), corre: pacman -S mingw-w64-ucrt-x86_64-gcc
    echo y agrega C:\msys64\ucrt64\bin al PATH.
    echo.
    goto :ffmpeg
)
call native\build_windows.bat

:ffmpeg
REM ---- 4. ffmpeg (convertir archivos y descargar) ----
where ffmpeg >nul 2>nul && goto :abrir
echo.
echo Falta ffmpeg: sin el solo se pueden abrir archivos WAV.
where winget >nul 2>nul || goto :abrir
choice /c SN /m "Instalarlo ahora con winget"
if errorlevel 2 goto :abrir
winget install --id Gyan.FFmpeg -e
echo Si la instalacion termino bien, cierra esta ventana y vuelve a abrir iniciar.bat.
pause
exit /b 0

:abrir
REM Rutas completas: funciona aunque algo de arriba haya cambiado de carpeta.
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0app\main.py"
exit /b 0

:error
echo.
echo Algo fallo. Revisa el mensaje de arriba.
pause
exit /b 1
