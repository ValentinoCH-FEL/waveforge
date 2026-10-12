#!/bin/bash
# Compila el motor nativo en Linux. Necesita g++ (sudo apt install g++
# en Debian/Ubuntu, o el equivalente de tu distro).
set -e
cd "$(dirname "$0")"

g++ -shared -fPIC -o libfiltro.so filtros/pasa_bajos.cpp filtros/pasa_altos.cpp filtros/normalizacion.cpp filtros/ruido_gate.cpp

echo "Listo: libfiltro.so generado."
