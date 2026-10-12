# Waveforge — Estudio de Audio

Separa cualquier canción (archivo o enlace de YouTube) en voz principal,
coros e instrumentos con IA, y deja mezclarlos y aplicarles efectos en
tiempo real. Los efectos se calculan en un motor de filtros en C++
(compartido entre Windows/Mac/Linux), conectado por `ctypes` a una
interfaz Tkinter en Python. Proyecto del curso Programación de
Interfaces y Dispositivos Periféricos.

## Qué hace

- Carga un archivo de audio o video (WAV, MP3, M4A, OGG, FLAC...) o
  descarga el audio de un enlace de YouTube. Máximo 10 minutos por
  canción.
- **Separa la canción automáticamente** al cargarla:
  - Demucs (modelo de 6 pistas) separa voz, batería, bajo, guitarra,
    piano y otros instrumentos.
  - Donde hay voz, un modelo karaoke (MDX-Net) la parte en **voz
    principal** y **coros**.
  - Solo aparecen las pistas que realmente suenan (si la canción no
    tiene coros o piano, esas pistas no se muestran).
  - Se procesa por trozos: los primeros segundos están listos en
    ~20 s y el resto se completa en segundo plano mientras ya se
    puede escuchar y editar.
  - Las últimas 5 canciones separadas quedan en caché: volver a
    cargarlas es instantáneo.
- Mezcla en vivo: volumen, silenciar (M) y solo (S) por pista, sin
  cortar el sonido.
- Efectos por pista, calculados por la librería nativa en C++:
  **pasa-bajos**, **pasa-altos**, **normalización** y **limpiar ruido
  de fondo** (noise gate).
- Grafica las ondas de cada pista, reproduce la mezcla o el original,
  y exporta la mezcla o cada pista por separado como WAV.
- Guarda un registro (`registro.log`) de lo que se hizo y cuándo.
- Modo alterno de datos CSV (Herramientas → Modo de datos), reusando
  el mismo motor nativo para una transformación numérica simple.

## Estructura

```
.
├── native/                  # motor en C++, mismo código para las 3 plataformas
│   ├── filtro.h              # contrato común que respeta cada filtro
│   ├── filtros/              # un archivo por filtro — así se agregan nuevos
│   ├── build_windows.bat     # genera filtro.dll
│   ├── build_mac.sh          # genera libfiltro.dylib
│   └── build_linux.sh        # genera libfiltro.so
├── app/                     # interfaz en Python
│   ├── main.py               # ventana Tkinter
│   ├── separador.py          # separación con IA por trozos + caché
│   ├── importador.py         # convertir archivos (ffmpeg) y descargar de YouTube (yt-dlp)
│   ├── reproductor.py        # mezclador en vivo (sounddevice)
│   ├── procesamiento.py      # aplicar filtros y mezclar pistas
│   ├── motor.py              # wrapper ctypes (carga la lib según el SO)
│   ├── filtros_registry.py   # catálogo de filtros disponibles
│   ├── audio_io.py           # cargar/guardar/validar WAV
│   ├── graficas.py           # ondas de cada pista (matplotlib)
│   ├── tema.py               # colores y estilos de la interfaz
│   └── modo_datos.py         # modo alterno CSV
├── requirements.txt
└── README.md
```

## Cómo correrlo

### 1. Compilar el motor nativo (una sola vez por plataforma)

Necesitas un compilador de C++. En Windows, instala MinGW vía
[MSYS2](https://www.msys2.org/) (`pacman -S mingw-w64-ucrt-x86_64-gcc`)
y agrega su `bin` al PATH. En Mac ya viene con las Command Line Tools
de Xcode (`xcode-select --install`). En Linux instala `g++` con el
gestor de paquetes de tu distro.

Luego, parado en la carpeta `native/`:

- Windows: `build_windows.bat`
- Mac: `./build_mac.sh`
- Linux: `./build_linux.sh`

Cada uno genera el archivo correspondiente (`filtro.dll`,
`libfiltro.dylib` o `libfiltro.so`) **dentro de `native/`** — ahí es
donde `motor.py` lo busca.

### 2. Instalar las dependencias

```
pip install -r requirements.txt
```

También hace falta **ffmpeg** (para convertir archivos y descargar de
YouTube): en Windows, `winget install ffmpeg`.

Tkinter viene incluido con Python en Windows y Mac. En Linux puede que
necesites instalarlo aparte (`sudo apt install python3-tk` en
Debian/Ubuntu).

**Python 3.14 en Windows:** `audio-separator` pide `diffq-fixed`, que
no compila en esa versión, y `beartype<0.19`, que no funciona en ella.
Instálalo sin dependencias y agrega las que necesita a mano:

```
pip install audio-separator --no-deps
pip install audioop-lts "beartype>=0.22" einops julius librosa ml_collections onnx-weekly onnx2torch-py313 onnxruntime pydub pyyaml resampy "rotary-embedding-torch<0.7" samplerate==0.1.0 scipy soundfile audioread
```

La primera vez que se separa una canción se descargan los modelos de
IA (~100 MB) en `~/.cache/estudio_audio_modelos`.

### 3. Ejecutar

```
python app/main.py
```

Sin tarjeta gráfica NVIDIA, la separación completa tarda más o menos
lo que dura la canción; con una NVIDIA (y PyTorch con CUDA) es varias
veces más rápida, sin cambiar nada del código.

## Cómo agregar un filtro nuevo (pensado para escalar)

1. Crea `native/filtros/tu_filtro.cpp` con la firma común
   `(float* entrada, float* salida, int cantidad, float parametro)` y
   decláralo en `native/filtro.h`.
2. Agrégalo a la lista de fuentes en los tres `build_*` y a la lista de
   nombres en `app/motor.py` (`_declarar_firmas`).
3. Agrega una entrada en `app/filtros_registry.py` con su etiqueta,
   descripción y rango de parámetro.

Nada en `main.py` necesita cambiar — por eso esta estructura es la base
pensada para, más adelante, convertir esto en un motor de DJ
personalizado (más efectos, mezcla, detección de BPM, etc.) sin
reescribir lo que ya funciona.
