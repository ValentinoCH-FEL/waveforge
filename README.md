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
    principal** y **coros**. En Windows corre en la tarjeta gráfica
    (DirectML: NVIDIA, AMD o Intel, también las integradas), ~3 veces más
    rápido que en el procesador, y al mismo tiempo que Demucs.
  - **Calidad alta** (menú Separación): la voz la saca Mel-Band RoFormer,
    bastante más limpia que Demucs. Sin tarjeta NVIDIA es lenta (varias
    veces lo que dura la canción); la primera vez descarga ~900 MB.
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
│   └── tema.py               # colores y estilos de la interfaz
├── iniciar.bat / iniciar.sh  # doble clic: instala lo que falte y abre la app
├── requirements.txt
└── README.md
```

## Cómo correrlo

**Windows:** doble clic en `iniciar.bat`. **Mac/Linux:** `./iniciar.sh`.

La primera vez prepara todo solo (tarda varios minutos) y después solo
abre la app:

1. Crea un entorno de Python en `.venv` dentro de la carpeta del proyecto.
2. Instala las dependencias de `requirements.txt`. Vuelve a hacerlo solo
   cuando ese archivo cambia (por ejemplo, tras un `git pull`).
3. Compila el motor de filtros en C++ si todavía no está compilado.
4. Si falta ffmpeg, ofrece instalarlo con `winget` (en Mac/Linux, avisa
   cómo instalarlo).

yt-dlp (descargas de YouTube) se mantiene al día solo: la app lo revisa
una vez al día en segundo plano, porque YouTube cambia seguido y las
versiones viejas dejan de descargar.

La primera vez que se separa una canción se descargan los modelos de
IA (~140 MB; ~900 MB más si usas la calidad alta) en
`~/.cache/estudio_audio_modelos`.

### Lo que el script no instala

- **Python 3.11 o más nuevo** (probado con 3.14):
  https://www.python.org/downloads/ o `winget install Python.Python.3.14`.
  En Linux puede hacer falta Tkinter aparte (`sudo apt install python3-tk`).
- **Compilador de C++**, solo para compilar el motor la primera vez. En
  Windows, MinGW vía [MSYS2](https://www.msys2.org/)
  (`pacman -S mingw-w64-ucrt-x86_64-gcc`), agregando `C:\msys64\ucrt64\bin`
  al PATH. En Mac viene con `xcode-select --install`; en Linux, `g++`.
  Sin compilador la app abre igual, pero sin efectos.

### A mano (sin los scripts)

```
python -m venv .venv
.venv\Scripts\activate          (Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt
pip install --no-deps audio-separator
native\build_windows.bat        (o native/build_mac.sh, native/build_linux.sh)
python app/main.py
```

`audio-separator` va con `--no-deps` porque sus dependencias declaradas
no instalan en Python 3.14 (`diffq-fixed` no compila y pide
`beartype<0.19`); `requirements.txt` ya lista las que de verdad necesita.

## Qué PC hace falta

Medido en un Ryzen 5 5600G (6 núcleos, gráficos Radeon integrados, sin
NVIDIA) con 16 GB de RAM:

| | Calidad rápida | Calidad alta (RoFormer) |
|---|---|---|
| Primeros segundos audibles | ~20 s | ~1-2 min |
| Canción de 4 min completa | ~3 min (0.7-0.8x) | ~25 min (~6x) |
| RAM usada por la IA | ~2.5 GB | ~3 GB |

- **Mínimo:** procesador de 4 núcleos, 8 GB de RAM, ~3 GB libres de
  disco (dependencias + modelos), Windows 10/11, macOS o Linux. Funciona,
  pero la separación tarda más que la canción.
- **Recomendado:** 6 núcleos o más y 16 GB de RAM; en Windows, cualquier
  tarjeta gráfica (aunque sea integrada) acelera el karaoke.
- **Ideal:** tarjeta NVIDIA con 6 GB o más de memoria y PyTorch con CUDA:
  la separación es varias veces más rápida que la canción y la calidad
  alta se vuelve práctica, sin cambiar nada del código.

Convertir el archivo no es lo lento: ffmpeg pasa una canción de 4 min a
WAV en ~0.2 s. El tiempo se va en la separación con IA.

## Cómo agregar un filtro nuevo (pensado para escalar)

1. Crea `native/filtros/tu_filtro.cpp` con la firma común
   `(float* entrada, float* salida, int cantidad, float parametro)` y
   decláralo en `native/filtro.h`.
2. Agrega una entrada en `app/filtros_registry.py` con su etiqueta,
   descripción, rango de parámetro y `funcion_nativa` (el nombre de la
   función en C++).
3. Vuelve a compilar con el `build_*` de tu sistema: compilan todos los
   `.cpp` de `native/filtros/` sin tener que anotarlos.

Nada en `main.py` ni en `motor.py` necesita cambiar — por eso esta estructura es la base
pensada para, más adelante, convertir esto en un motor de DJ
personalizado (más efectos, mezcla, detección de BPM, etc.) sin
reescribir lo que ya funciona.
