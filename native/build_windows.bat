@echo off
REM Compila el motor nativo en Windows usando g++ (MinGW).
REM Si no lo tienes instalado: instala MSYS2 (https://www.msys2.org/)
REM y desde su terminal corre: pacman -S mingw-w64-ucrt-x86_64-gcc
REM Luego agrega su carpeta bin al PATH y corre este script
REM desde una terminal normal (cmd o PowerShell), desde cualquier carpeta.

REM pushd/popd: compila en native\ sin dejar a quien lo llama (iniciar.bat) parado ahi.
pushd "%~dp0"

g++ -shared -o filtro.dll -O2 -static filtros\*.cpp

set "RESULTADO=%errorlevel%"
popd

if %RESULTADO% equ 0 (
    echo Listo: filtro.dll generado.
) else (
    echo Error al compilar. Revisa que g++ este en el PATH.
)
exit /b %RESULTADO%
