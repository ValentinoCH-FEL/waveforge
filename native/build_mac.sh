#!/bin/bash
# Compila el motor nativo en macOS. clang++ ya viene con las
# Command Line Tools de Xcode (xcode-select --install si falta).
set -e
cd "$(dirname "$0")"

clang++ -shared -fPIC -o libfiltro.dylib -O2 filtros/*.cpp

echo "Listo: libfiltro.dylib generado."
