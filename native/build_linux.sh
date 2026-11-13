#!/bin/bash
# Compila el motor nativo en Linux. Necesita g++ (sudo apt install g++
# en Debian/Ubuntu, o el equivalente de tu distro).
set -e
cd "$(dirname "$0")"

g++ -shared -fPIC -o libfiltro.so -O2 filtros/*.cpp

echo "Listo: libfiltro.so generado."
