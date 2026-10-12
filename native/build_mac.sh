#!/bin/bash
# Compila el motor nativo en macOS. clang++ ya viene con las
# Command Line Tools de Xcode (xcode-select --install si falta).
set -e
cd "$(dirname "$0")"

clang++ -shared -fPIC -o libfiltro.dylib filtros/pasa_bajos.cpp filtros/pasa_altos.cpp filtros/normalizacion.cpp filtros/ruido_gate.cpp

echo "Listo: libfiltro.dylib generado."
