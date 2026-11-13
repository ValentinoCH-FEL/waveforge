#!/bin/bash
# Waveforge en Mac/Linux: ./iniciar.sh  (la primera vez prepara todo solo)
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    echo "Creando el entorno de Python en .venv ..."
    python3 -m venv .venv
fi

if ! cmp -s requirements.txt .venv/requirements.instalado; then
    echo "Instalando dependencias. La primera vez tarda varios minutos..."
    .venv/bin/python -m pip install --disable-pip-version-check -q --upgrade pip
    .venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
    .venv/bin/python -m pip install --disable-pip-version-check -q --no-deps audio-separator
    cp requirements.txt .venv/requirements.instalado
fi

if [ ! -f native/libfiltro.so ] && [ ! -f native/libfiltro.dylib ]; then
    if [ "$(uname)" = "Darwin" ]; then ./native/build_mac.sh; else ./native/build_linux.sh; fi
fi

command -v ffmpeg >/dev/null || echo "Aviso: falta ffmpeg (brew install ffmpeg / sudo apt install ffmpeg)."

exec .venv/bin/python app/main.py
