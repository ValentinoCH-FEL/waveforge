# Waveforge — Documentación del proyecto

**Felipe Valentino Calderón Hurtado** · Programación de Interfases y Dispositivos Periféricos · 32688 · UTP 2026-II · Docente: Bryan Luis Solano Condor

Los requerimientos funcionales y no funcionales, con un mapa mental, están en [REQUERIMIENTOS.md](REQUERIMIENTOS.md).

## Resumen

Waveforge es un estudio de audio de escritorio que **convierte** cualquier canción, la **separa** en hasta siete pistas con inteligencia artificial y permite **moldearla** con edición por tramos y efectos en C++.

| Dato | Valor |
| --- | --- |
| Autor | Felipe Valentino Calderón Hurtado (proyecto individual) |
| Curso | Programación de Interfases y Dispositivos Periféricos · 32688 · UTP 2026-II |
| Docente | Bryan Luis Solano Condor |
| Repositorio | [github.com/ValentinoCH-FEL/waveforge](https://github.com/ValentinoCH-FEL/waveforge) |
| Lenguajes | Python 3.14 (interfaz y lógica) · C++ (efectos) |
| Tamaño | ~4 000 líneas: ~3 300 en Python, ~270 en C++, el resto en scripts |
| Versión documentada | rama `main`; el modo DJ está en la rama `funcion/modo-dj` |

La aplicación tiene tres pestañas:

1. **Separación con IA**: se carga un archivo de audio o video, o un enlace, y la IA lo divide en pistas: voz principal, coros, batería, bajo, guitarra, piano y otros.
2. **Moldear**: un editor con una pista por carril, donde se elige un tramo y se le aplica volumen, silencio, fades o un efecto; después se exporta a WAV.
3. **DJ**: en `main` aparece vacía; el modo DJ se desarrolla en una rama aparte (sección Modo DJ).

## Instalación y requisitos

La instalación es automática. En Windows se hace doble clic en `iniciar.bat`; en Linux o Mac se ejecuta `iniciar.sh`. El script:

1. Crea `.venv`, un Python aislado del resto de la computadora, si todavía no existe.
2. Instala las librerías de `requirements.txt` solo si cambió la lista.
3. Compila los efectos en C++ (`native/build_windows.bat`) si falta `filtro.dll`.
4. Abre la aplicación (`app/main.py`).

La primera vez que se separa una canción, la aplicación descarga los modelos de IA (unos cientos de MB) y los guarda para las siguientes veces.

### Requisitos

| | Mínimo | Recomendado |
| --- | --- | --- |
| Procesador | 4 núcleos (x86-64) | 6 núcleos o más (por ejemplo, Ryzen 5) |
| Memoria RAM | 8 GB | 16 GB |
| Tarjeta gráfica | No es necesaria (todo corre en CPU) | Cualquier GPU con DirectML en Windows, o NVIDIA con CUDA |
| Disco | ~3 GB (librerías y modelos) | SSD |
| Software | Python 3.10 o superior, ffmpeg, compilador g++ (MinGW en Windows) | Python 3.14 |

ffmpeg convierte los formatos, y yt-dlp, que se instala con las librerías, descarga desde enlaces. Sin tarjeta gráfica la separación funciona igual, pero tarda más.

## Arquitectura

El programa está organizado en tres capas. Cada capa solo llama a la de abajo.

| Capa | Qué contiene | Archivos |
| --- | --- | --- |
| **Interfaz** (Tkinter) | Ventanas, pestañas, botones y dibujo de las ondas | `main.py`, `editor.py`, `widgets.py`, `tema.py` |
| **Lógica** (Python puro, sin Tk) | Conversión, separación, ediciones, reproducción y huella de voz | `importador.py`, `audio_io.py`, `separador.py`, `ediciones.py`, `reproductor.py`, `identificador.py`, `procesamiento.py`, `filtros_registry.py` |
| **Motor** (C++) | Efectos que recorren cada muestra | `motor.py` (puente ctypes) → `filtro.dll` |

La regla principal es que **la lógica nunca importa Tkinter**. Gracias a eso cada módulo se puede probar desde la consola sin abrir la ventana.

### Trabajo en segundo plano

Todo lo lento (convertir, descargar, separar, identificar la voz) corre en un `threading.Thread`. El resultado vuelve a la ventana con `root.after(0, …)`, porque Tkinter solo se puede tocar desde el hilo principal.

Cada carga de canción incrementa un contador `_sesion`. Un hilo viejo compara su número con el actual y, si no coincide, se cancela y descarta su resultado. Así cargar otra canción a mitad de la separación no mezcla las pistas.

## Estructura de archivos

### `app/`: código en Python

| Archivo | Líneas | Qué hace |
| --- | --- | --- |
| `main.py` | 492 | Punto de entrada: ventana, pestañas y pestaña Separación con IA |
| `editor.py` | 1114 | Pestaña Moldear: carriles, zoom, selección de tramos, deshacer y exportar |
| `ediciones.py` | 180 | Aplica las ediciones sobre una copia del audio; `Historial` y `Picos` |
| `separador.py` | 481 | Separación con IA por trozos y en dos etapas paralelas |
| `identificador.py` | 180 | Huella de la voz del cantante (WeSpeaker) |
| `importador.py` | 215 | Convierte con ffmpeg y descarga con yt-dlp |
| `audio_io.py` | 123 | Lee y guarda WAV de 16 bits |
| `reproductor.py` | 156 | Reproduce varias pistas a la vez con sounddevice |
| `procesamiento.py` | 46 | Aplica un filtro y mezcla las pistas al exportar |
| `motor.py` | 82 | Carga `filtro.dll` y llama a los filtros con ctypes |
| `filtros_registry.py` | 106 | Catálogo de efectos y sus controles |
| `widgets.py` | 255 | Botones animados, vúmetro y ayudas emergentes |
| `tema.py` | 209 | Colores y estilos del tema oscuro |

### `native/`: motor en C++

| Archivo | Qué hace |
| --- | --- |
| `filtro.h` | Firma común: `void filtro(const float* entrada, float* salida, int n, float parametro)` |
| `filtros/pasa_bajos.cpp` | Deja pasar los graves |
| `filtros/pasa_altos.cpp` | Deja pasar los agudos |
| `filtros/normalizacion.cpp` | Sube el volumen hasta que el pico llegue al máximo |
| `filtros/ruido_gate.cpp` | Silencia lo que está por debajo de un umbral |
| `filtros/ecualizador.cpp` | Biquads de graves, medios y agudos |
| `build_windows.bat` · `build_linux.sh` · `build_mac.sh` | Compilan todo en `filtro.dll` / `.so` / `.dylib` |

### Raíz

| Archivo | Qué hace |
| --- | --- |
| `iniciar.bat` · `iniciar.sh` | Instalación automática y arranque |
| `requirements.txt` | Librerías de Python |
| `README.md` | Presentación e instrucciones |
| `.gitignore` | Excluye `.venv`, `__pycache__`, `filtro.dll`, `registro.log` y la caché |
| `.gitattributes` | Fija los saltos de línea de `.sh` (LF) y `.bat` (CRLF) |

## Flujo de una canción

1. **Carga** (`main.py`): el usuario elige un archivo o pega un enlace.
2. **Conversión** (`importador.py`): ffmpeg lo lleva a WAV de 44 100 Hz, estéreo y 16 bits; si es un enlace, yt-dlp lo descarga primero.
3. **Lectura** (`audio_io.py`): el WAV pasa a dos arreglos `float32` (izquierdo y derecho) con valores entre −1 y 1.
4. **Separación** (`separador.py`): la IA genera hasta siete pistas. Si la canción ya se separó antes, se toma de la caché.
5. **Edición** (`editor.py` + `ediciones.py`): las pistas se abren solas en Moldear y cada cambio se guarda como una operación.
6. **Escucha** (`reproductor.py`): se reproducen todas las pistas juntas, con su volumen, mute y solo.
7. **Exportación** (`procesamiento.py` + `audio_io.py`): se mezclan las pistas activas y se guarda un WAV.

Los pasos 2 y 4 corren en segundo plano y muestran su avance en una barra de progreso.

## Conversión

Todo lo que entra se lleva a un único formato. Así el resto del programa no tiene que distinguir entre MP3, FLAC, MP4 o un enlace.

```python
comando = [
    "ffmpeg", "-y", "-i", ruta_origen,
    "-ar", str(MUESTREO_NATIVO), "-ac", "2",
    "-sample_fmt", "s16", destino,
]
resultado = subprocess.run(comando, capture_output=True)
```

- `-ar 44100`: 44 100 muestras por segundo.
- `-ac 2`: dos canales (estéreo).
- `s16`: 16 bits por muestra.

Después `audio_io.cargar_wav` lee los bytes como `int16` y los divide entre 32 768 para dejarlos entre −1 y 1. Como las muestras vienen intercaladas (I, D, I, D…), `[0::2]` toma el canal izquierdo y `[1::2]` el derecho. Al guardar se hace lo contrario: se recorta a ±1, se multiplica por 32 767 y se intercalan los canales.

Para los enlaces, `importador.py` usa yt-dlp con extracción de audio y entrega el archivo descargado a la misma conversión. Antes, `_url_de_un_video` deja el enlace apuntando a un solo video: los «Mix» de YouTube (`list=RD…`) son listas que YouTube no deja leer, y su id es «RD» + el id del video, así que de ahí se recupera. Si YouTube no deja ver el video (borrado, privado o bloqueado), el error lo dice tal cual.

## Separación con IA

`separador.py` divide la canción en hasta siete pistas: **voz principal, coros, batería, bajo, guitarra, piano y otros**.

### Dos modelos, dos etapas

| Etapa | Modelo | Dónde corre | Qué produce |
| --- | --- | --- | --- |
| 1 | Demucs `htdemucs_6s` (PyTorch) | CPU | voz, batería, bajo, guitarra, piano, otros |
| 1 (calidad alta) | Mel-Band RoFormer saca la voz; Demucs reparte el resto | CPU (lento sin NVIDIA) | voz más limpia + instrumentos |
| 2 | UVR-MDX-NET Karaoke 2 (ONNX) | GPU, si hay | voz principal; **coros = voz − principal** |

Hay dos calidades: **Rápida**, donde Demucs saca la voz, y **Alta**, donde la saca RoFormer (unos 900 MB que se descargan la primera vez). Los trozos sin voz se saltan en la etapa 2, y las pistas que quedan por debajo de −40 dB respecto de la canción (−30 dB para los coros) no se muestran. Las canciones de más de 10 minutos se rechazan al cargarlas.

Como los coros se calculan como resta, la suma de todas las pistas da siempre la canción original.

### Trozos y paralelismo

La canción se procesa por trozos: el primero dura 15 s, para que algo esté listo en segundos, y los siguientes 30 s. Cada trozo lleva 2 s de contexto a cada lado, que luego se descartan, y los trozos se unen con un fundido de 0,5 s para que no se note el corte. Después de cada trozo, `separar_por_partes` avisa a la interfaz, así se puede escuchar el principio mientras se separa el resto.

Las dos etapas trabajan a la vez, comunicadas por una `queue.Queue(maxsize=1)`: mientras Demucs separa el trozo *n* en la CPU, el karaoke procesa la voz del trozo *n − 1* en la GPU. El tamaño 1 de la cola evita acumular trozos en memoria.

### Tarjeta gráfica sin NVIDIA

```python
disponibles = onnxruntime.get_available_providers()
preferidos = [p for p in ("CUDAExecutionProvider", "DmlExecutionProvider") if p in disponibles]
return preferidos[:1] + ["CPUExecutionProvider"]
```

Se prueba CUDA (NVIDIA), luego DirectML (cualquier GPU en Windows, incluso integrada) y por último la CPU. Con DirectML el modelo de voz bajó de ~26 s a menos de 8 s por cada 30 s de canción. Los hilos de PyTorch se limitan a los núcleos físicos menos uno, para que la ventana siga respondiendo.

### Modo concierto

Con la casilla *Grabación en vivo* activada, antes del karaoke se aplica **UVR-MDX-NET Crowd HQ 1**, que quita el público (gritos y aplausos) de la voz.

### Caché

El resultado se guarda como `.npz` usando un hash del audio y de la calidad. Se conservan las últimas 5 canciones; abrir una de ellas es instantáneo.

### Detalle técnico

`audio-separator` se usa con `use_soundfile=True`. Sin esa opción llamaba a ffmpeg en cada trozo, y en Windows el subproceso fallaba con el error `0xC0000142` a mitad de la canción.

## Editor Moldear

Cada pista ocupa un carril con su forma de onda y sus controles: volumen, mute y solo. Con el ratón se selecciona un tramo arrastrando, se hace zoom con la rueda y se mueve la vista; un clic sin arrastrar solo mueve el cabezal, sin modificar la pista.

### Edición no destructiva

El audio original nunca se modifica. Cada cambio se guarda como una operación en la lista de su pista:

```python
{"tipo": "ganancia", "i0": 441000, "i1": 882000, "db": -6.0}
```

`ediciones.renderizar` copia el original (`copy=True`) y aplica las operaciones en orden. Los tipos son:

| Operación | Efecto en el tramo `[i0, i1)` |
| --- | --- |
| `ganancia` | Multiplica por `10 ** (db / 20)` (−6 dB ≈ ×0,5) |
| `silencio` | Pone ceros |
| `fade_in` / `fade_out` | Rampa lineal de 0 a 1, o de 1 a 0 |
| `efecto` | Pasa el tramo por un efecto de C++ |
| `curva` | Volumen variable que genera el identificador de voz |

`_rampa_bordes` suaviza unos pocos milisegundos en los extremos del tramo, para que no suene un clic al cambiar el volumen de golpe.

### Deshacer y rehacer

`Historial` guarda las listas de operaciones anteriores. Deshacer devuelve la lista anterior y vuelve a renderizar. Como se parte siempre del original, el resultado es idéntico bit a bit al estado previo. También se registran en el historial quitar las ediciones de un tramo y eliminar una pista, así que también se pueden deshacer.

El botón **A/B original** alterna entre la versión editada y la original, para comparar.

### Dibujo rápido de las ondas

`Picos` resume el audio en pares mínimo/máximo cada 256 muestras. Al dibujar se usa ese resumen y no los millones de muestras, por eso redibujar cinco minutos de audio tarda unos 7 ms.

### Identificador de voz en el editor

La pista de voz principal tiene una opción para quedarse solo con el cantante, que se explica en la sección *Identificador de voz*.

### Exportar

Se mezclan las pistas que no están en mute, con su volumen y sus ediciones, se recorta a ±1 y se guarda un WAV. También se puede exportar **cada pista por separado**: un WAV por pista en la carpeta que se elija.

## Efectos en C++

Los efectos recorren cada muestra del audio: una canción de 4 minutos tiene más de 10 millones. Por eso están escritos en C++ y no en Python.

### El contrato común

Todos los filtros tienen la misma firma (`filtro.h`):

```cpp
extern "C" void nombre(const float* entrada, float* salida, int n, float parametro);
```

`motor.py` carga la librería con `ctypes.CDLL` y llama al filtro por su nombre, pasándole el puntero del arreglo de numpy. Así el audio no se copia. Para agregar un efecto basta con escribir su `.cpp`, compilar y anotarlo en `filtros_registry.py` con sus controles.

### Los filtros

| Filtro | Cómo funciona |
| --- | --- |
| Pasa-bajos | Promedio exponencial: `y[i] = y[i−1] + α·(x[i] − y[i−1])`. Con un α más chico se suaviza más y quedan solo los graves |
| Pasa-altos | Filtro RC: `y[i] = α·(y[i−1] + x[i] − x[i−1])`. Solo pasan los cambios rápidos, o sea los agudos |
| Normalización | Busca el pico máximo y multiplica todo por `objetivo / pico` |
| Puerta de ruido | Abre cuando el nivel supera el umbral (`10^(dB/20)`) y cierra al bajar del 70 % de ese valor (histéresis). Se mantiene abierta 80 ms, abre en 3 ms, cierra en 150 ms y atenúa hasta ~−26 dB, sin llegar a cortar del todo |
| Ecualizador | Tres biquads en cadena: graves 150 Hz (*shelf*), medios 1 kHz (*peaking*) y agudos 5 kHz (*shelf*) |

### El ecualizador por dentro

Cada banda es un filtro *biquad* con los coeficientes de Robert Bristow-Johnson (RBJ). Para cada muestra:

```cpp
y = b0*x + b1*x1 + b2*x2 - a1*y1 - a2*y2;
```

Se usa la muestra actual, las dos anteriores de la entrada (`x1`, `x2`) y las dos anteriores de la salida (`y1`, `y2`). Los coeficientes salen de la frecuencia de la banda y de la ganancia:

- `A = 10^(dB/40)` y `w0 = 2π·f / 44100`
- `α = sin(w0) / (2Q)`
- graves y agudos usan las fórmulas de *shelf*; los medios, la de *peaking*

Todos los coeficientes se dividen por `a0` antes de usarlos.

## Reproducción

`reproductor.py` abre la salida de audio con `sounddevice.OutputStream`:

- `blocksize=1024`: la tarjeta de sonido pide bloques de 1 024 muestras (~23 ms).
- `latency=0.25`: un margen de 250 ms para que no haya cortes aunque la PC esté ocupada.
- `callback`: una función que llena cada bloque.

En cada bloque, el callback suma todas las pistas activas, cada una multiplicada por su volumen. Cuando el volumen cambia, se aplica una **rampa** dentro del bloque en lugar de un salto, para evitar el chasquido. Al final `np.clip(…, -1, 1)` evita la saturación.

Mute y solo solo cambian el peso de la pista, así que no detienen la reproducción. El cabezal del editor se actualiza con la posición que reporta el reproductor.

## Identificador de voz

En grabaciones en vivo, la pista de voz puede traer al público cantando u otras voces. `identificador.py` deja solo al cantante:

1. El usuario marca un tramo donde se oye **solo** al cantante.
2. De ese tramo se obtiene una **huella de voz**, un vector de 256 números, con WeSpeaker ResNet34-LM (ONNX).
3. La pista de voz se recorre en ventanas de 1,5 s cada 0,25 s. Cada ventana se compara con la huella mediante la **similitud coseno**.
4. Donde la voz no se parece a la del cantante, el volumen se baja.

### Entrada del modelo

El modelo espera el *fbank* de Kaldi: 80 bandas mel, audio a 16 kHz y ventanas de 25 ms cada 10 ms. Se calcula en numpy (ventana de Povey, banco de filtros mel triangulares, logaritmo de la energía) y da el mismo resultado que `torchaudio.compliance.kaldi.fbank`. Así no hace falta instalar torchaudio solo para eso.

### Exigencia

La comparación, que es lo costoso, se hace una sola vez. Después, el control **exigencia** (0 a 1) define desde qué parecido se considera que canta el artista (`0.15 + 0.55 · exigencia`). La curva de volumen se recalcula al instante y se suaviza muestra por muestra. Las ventanas por debajo de −45 dB son silencio y no se tocan.

## Modelos, librerías y herramientas

### Modelos de IA

| Modelo | Uso | Formato | Licencia |
| --- | --- | --- | --- |
| Demucs `htdemucs_6s` (Meta) | Separación en 6 pistas | PyTorch | MIT |
| Mel-Band RoFormer | Voz en calidad alta | PyTorch | MIT |
| UVR-MDX-NET Karaoke 2 | Voz principal y coros | ONNX | MIT (UVR) |
| UVR-MDX-NET Crowd HQ 1 | Quitar el público | ONNX | MIT (UVR) |
| WeSpeaker ResNet34-LM | Huella de voz | ONNX | CC-BY-4.0 |

Los modelos se descargan la primera vez y se guardan en `~/.cache/estudio_audio_modelos`.

### Librerías de Python

| Librería | Para qué |
| --- | --- |
| `numpy` | Todo el audio son arreglos `float32` |
| `tkinter` | Interfaz (viene con Python) |
| `sounddevice` | Salida de audio en tiempo real |
| `soundfile` | Lectura y escritura de audio para los modelos |
| `torch`, `demucs-infer` | Modelo Demucs |
| `audio-separator` | Modelos UVR y RoFormer (se instala con `--no-deps`, porque sus dependencias declaradas no instalan en Python 3.14) |
| `onnxruntime-directml` / `onnxruntime` | Modelos ONNX: en la GPU en Windows, en la CPU en Linux y Mac |
| `librosa` | Análisis de audio que usan los modelos |
| `yt-dlp` | Descarga desde enlaces; la app lo actualiza sola una vez al día |

### Herramientas externas

- **ffmpeg**: conversión de formatos.
- **g++ (MinGW)**: compila los filtros de C++.
- **Git y GitHub**: control de versiones.
- **VS Code**: editor de código.

## Pruebas y resultados

Las pruebas se hicieron en la computadora del autor: un Ryzen 5 con gráfica Radeon integrada y sin NVIDIA. Cada módulo de lógica se probó desde la consola, y la interfaz con eventos simulados.

| Prueba | Resultado |
| --- | --- |
| Volumen −6 dB en un tramo | El tramo baja exactamente −6 dB y fuera del tramo el audio queda idéntico |
| Deshacer después de varias ediciones | El audio vuelve a ser igual bit a bit al original |
| Redibujar 5 minutos de ondas | ~7 ms |
| Modelo karaoke por cada 30 s de audio | ~26 s en CPU → menos de 8 s con DirectML |
| Retraso de la interfaz durante la separación | de hasta 15 ms a ~2 ms |
| Separación completa (calidad rápida) | Menos tiempo que lo que dura la canción |
| fbank en numpy contra torchaudio | Mismo resultado |
| Separación de una canción larga en Windows | Ya no se corta a la mitad (`use_soundfile=True`) |

Las mediciones se hicieron con audio generado (tonos y ruido) y con canciones de prueba. No se midió con métricas formales (SDR) la calidad de la separación con canciones reales.

## Control de versiones

El proyecto está en GitHub: [ValentinoCH-FEL/waveforge](https://github.com/ValentinoCH-FEL/waveforge). La rama `main` tiene 28 commits, todos del autor. Esta documentación y los requerimientos están en la rama `docs/documentacion-y-requerimientos`.

Cada mejora se hizo en una rama propia y entró a `main` con un pull request:

| PR | Rama | Contenido | Estado |
| --- | --- | --- | --- |
| #1 | `limpieza/ordenar-codigo` | Arreglos de interfaz e importación; una sola lista de filtros | Merged |
| #2 | `mejora/roformer-velocidad` | Karaoke en la GPU, en paralelo con Demucs; calidad alta con RoFormer | Cerrado: su contenido entró con el #3 |
| #3 | `mejora/instalacion-automatica` | `iniciar.bat` / `iniciar.sh` y yt-dlp siempre al día | Merged |
| #4 | `mejora/pestanas-y-reproduccion` | Editor Moldear, pestañas, modo concierto e identificador de voz | Merged |

La rama `funcion/modo-dj` tiene 6 commits más sobre `main` y no está integrada, porque el modo DJ sigue en desarrollo.

Los mensajes de commit están en español y describen el cambio, por ejemplo: *Acelera la separación: karaoke en la GPU, en paralelo con Demucs*. `.gitignore` deja fuera todo lo que cada computadora genera (`.venv`, `__pycache__`, `filtro.dll`, `registro.log`, cachés), y `.gitattributes` fija los saltos de línea de los scripts.

## Modo DJ (rama `funcion/modo-dj`)

El objetivo es un DJ automático: toma una biblioteca de canciones, por ejemplo desde un USB, arma una sesión continua y crea transiciones únicas usando las pistas separadas. Son unas 1 650 líneas nuevas en cinco módulos, sin Tkinter salvo la interfaz:

| Archivo | Líneas | Qué hace |
| --- | --- | --- |
| `dj_analisis.py` | 301 | BPM, grilla de beats, compás, tonalidad (perfiles de Krumhansl → rueda Camelot) y energía. Cada canción se analiza una vez y queda guardada |
| `dj_motor.py` | 203 | Transiciones con stems: por ejemplo, entra la batería de la siguiente mientras sale la voz de la actual |
| `dj_sesion.py` | 460 | Sesión continua: elige la siguiente canción por tempo, tonalidad y energía, sigue un plan por géneros y descarta lo ya usado |
| `dj_ui.py` | 464 | Pestaña DJ: biblioteca, cabina y votos 👍 / 👎 |
| `dj_aprender.py` | 225 | Aprende el estilo de un DJ a partir del audio de una mezcla |

### Decisiones clave

- **Solo se separa el tramo de la transición**, no la canción entera. Así la siguiente mezcla está lista a tiempo, aun sin tarjeta NVIDIA.
- **Recetas de stems**: cada tipo de transición define qué pista entra y cuál sale en cada compás, alineadas a la grilla de beats.
- **Compatibilidad armónica**: se prefieren canciones vecinas en la rueda Camelot (la misma clave, ±1, o el cambio mayor/menor).
- **Votos**: los 👍 y 👎 ajustan qué transiciones y canciones se eligen después.

### Aprender de un DJ

A partir del audio de una mezcla, `dj_aprender.py` compara cómo suena el medio minuto anterior con el siguiente (armonía y timbre). Donde esa diferencia hace un pico, cambió la canción. De ahí obtiene cada cuánto cambia de canción, cuánto duran las transiciones en beats, qué técnica usa (corte, fundido o quitar graves) y cuánto varía el tempo. El perfil se guarda en JSON (`~/.waveforge/dj_perfiles/`) y la sesión lo aplica al mezclar. Es una estimación: no conoce las canciones originales por separado.

## Limitaciones y trabajo futuro

### Limitaciones actuales

- **Sin pruebas automáticas** en el repositorio: las verificaciones se hicieron a mano y con scripts sueltos.
- **Calidad de separación sin medir** con métricas formales y canciones reales.
- **Velocidad**: sin GPU, la calidad alta (RoFormer) tarda varias veces lo que dura la canción.
- **Duración máxima** de 10 minutos por canción.
- **Exportación** solo en WAV de 16 bits a 44,1 kHz.
- **Identificador de voz**: necesita un tramo donde el cantante esté solo, y falla con voces muy parecidas.
- El **modo DJ** no está integrado a `main`, y aprender de una mezcla da una estimación, no una copia exacta del estilo.

### Trabajo futuro

1. Agregar pruebas automáticas (`pytest`) para `ediciones`, `audio_io`, los filtros de C++ y el identificador.
2. Medir la calidad de la separación (SDR) con un conjunto de canciones de referencia.
3. Exportar también a MP3 y FLAC.
4. Terminar el modo DJ, probarlo con bibliotecas reales e integrarlo a `main` con un pull request.
5. Agregar más efectos en C++, como reverb y compresor, usando el mismo contrato de `filtro.h`.
