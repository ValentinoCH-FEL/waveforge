"""
dj_analisis.py — lo que el DJ necesita saber de cada canción, sin nada de Tkinter.

  - decodificar(): cualquier archivo de audio o video a memoria (ffmpeg), sin temporales.
  - analisis_rapido(): BPM, tonalidad, energía, duración y etiquetas con solo 60 s de la canción (para llenar la biblioteca
    rápido). Se guarda en disco: cada canción se analiza una sola vez.
  - analisis_completo(): la grilla de beats de toda la canción, el compás y dónde empieza y
    termina de verdad (para decidir dónde mezclar).

Tempo y beats salen de librosa; la tonalidad, de comparar el croma con los perfiles de
Krumhansl y se expresa en la rueda Camelot que usan los DJs (8A, 9B...).
"""

import json
import os
import subprocess
import time

import numpy as np

SR = 44100            # todo el DJ trabaja a esto, en estéreo
SR_ANALISIS = 22050
# Solo audio: con .mp4 la biblioteca se llenaba de videos de celular tomados como canciones.
EXTENSIONES = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".aiff", ".aif", ".wma")

CARPETA_DATOS = os.path.join(os.path.expanduser("~"), ".waveforge")
_RUTA_CACHE = os.path.join(CARPETA_DATOS, "dj_biblioteca.json")

_MAYOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MENOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
_NOTAS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# número Camelot de cada tónica (índice = nota, 0 = Do)
_CAMELOT_MAYOR = [8, 3, 10, 5, 12, 7, 2, 9, 4, 11, 6, 1]
_CAMELOT_MENOR = [5, 12, 7, 2, 9, 4, 11, 6, 1, 8, 3, 10]


class ErrorAnalisis(Exception):
    pass


# ---------- leer audio ----------

def _ffmpeg(argumentos):
    """Corre ffmpeg y devuelve su salida. En Windows el arranque falla a veces: se reintenta."""
    ultimo = ""
    for intento in range(3):
        try:
            r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin"] + argumentos, capture_output=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except FileNotFoundError:
            raise ErrorAnalisis("No se encontró ffmpeg. Instálalo con: winget install ffmpeg")
        if r.returncode == 0:
            return r.stdout
        ultimo = r.stderr.decode("utf-8", "replace").strip()[-300:]
        time.sleep(0.2 * (intento + 1))
    raise ErrorAnalisis(f"ffmpeg no pudo leer el archivo: {ultimo or 'error desconocido'}")


def decodificar(ruta):
    """Devuelve la canción entera como float32 [muestras, 2] a 44100 Hz."""
    crudo = _ffmpeg(["-i", ruta, "-vn", "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"])
    audio = np.frombuffer(crudo, dtype=np.float32).reshape(-1, 2)
    if len(audio) < SR * 5:
        raise ErrorAnalisis("El archivo dura menos de 5 segundos.")
    return audio


def _decodificar_trozo(ruta, desde_s, segundos):
    args = ["-ss", str(desde_s)] if desde_s else []
    crudo = _ffmpeg(args + ["-i", ruta, "-vn", "-t", str(segundos), "-f", "f32le", "-ac", "1",
                            "-ar", str(SR_ANALISIS), "-"])
    return np.frombuffer(crudo, dtype=np.float32)


def metadatos(ruta):
    """Duración y etiquetas del archivo (artista, título, género), leídas con ffprobe."""
    datos = {"dur": 0.0, "artista": None, "titulo": None, "genero": None}
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration:format_tags=artist,title,genre", "-of", "json", ruta],
                           capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        formato = json.loads(r.stdout.decode("utf-8", "replace") or "{}").get("format", {})
        datos["dur"] = float(formato.get("duration") or 0.0)
        etiquetas = {k.lower(): str(v).strip() for k, v in (formato.get("tags") or {}).items()}
        datos["artista"] = etiquetas.get("artist") or None
        datos["titulo"] = etiquetas.get("title") or None
        datos["genero"] = etiquetas.get("genre") or None
    except (OSError, ValueError):
        pass
    return datos


def de_nombre(ruta):
    """(artista, título) sacados del nombre del archivo: "03 - Artista - Título.mp3"."""
    partes = [x.strip() for x in os.path.splitext(os.path.basename(ruta))[0].split(" - ")]
    if partes and partes[0].isdigit():
        partes = partes[1:]
    if len(partes) >= 2:
        return partes[0], " - ".join(partes[1:])
    return None, (partes[0] if partes else os.path.basename(ruta))


# ---------- tempo y tonalidad ----------

def _tonalidad(mono):
    import librosa

    croma = librosa.feature.chroma_stft(y=mono, sr=SR_ANALISIS, n_fft=4096, hop_length=2048).mean(axis=1)
    mejor = (-2.0, 0, True)
    for tonica in range(12):
        for mayor, perfil in ((True, _MAYOR), (False, _MENOR)):
            r = float(np.corrcoef(croma, np.roll(perfil, tonica))[0, 1])
            if r > mejor[0]:
                mejor = (r, tonica, mayor)
    _, tonica, mayor = mejor
    numero = (_CAMELOT_MAYOR if mayor else _CAMELOT_MENOR)[tonica]
    return f"{_NOTAS[tonica]}{'' if mayor else 'm'}", f"{numero}{'B' if mayor else 'A'}"


def _bpm_y_beats(mono):
    import librosa

    tempo, cuadros = librosa.beat.beat_track(y=mono, sr=SR_ANALISIS, hop_length=512)
    return float(np.atleast_1d(tempo)[0]), librosa.frames_to_time(cuadros, sr=SR_ANALISIS, hop_length=512)


_HOP_GOLPES = 50  # la curva de golpes tiene un valor cada 50 muestras (~2.3 ms a 22050 Hz)


def _curva_golpes(mono):
    """Dónde hay golpes: subidas bruscas de energía, sobre todo en los graves (el bombo marca
    el beat; los platillos suelen ir a contratiempo y confundirían la fase)."""
    from scipy.signal import butter, sosfilt

    def subidas(x):
        n = len(x) // _HOP_GOLPES
        energia = np.sqrt(np.mean(np.square(x[:n * _HOP_GOLPES].reshape(n, _HOP_GOLPES)), axis=1))
        suave = np.convolve(energia, np.ones(4) / 4, mode="same")
        d = np.maximum(np.diff(suave, prepend=suave[0]), 0.0)
        return d / (d.max() + 1e-12)

    graves = sosfilt(butter(2, 150, btype="low", fs=SR_ANALISIS, output="sos"), mono)
    return subidas(graves) + 0.3 * subidas(mono)


def _grilla_fina(mono, bpm_aprox):
    """Para música de tempo fijo: busca el BPM (±6 % del aproximado, en pasos de 0.02) y la fase
    cuya grilla de beats cae mejor sobre los golpes. Devuelve (bpm, primer_beat_s, contraste);
    contraste alto = la grilla calza de verdad (tempo fijo); bajo = tempo variable o sin ritmo."""
    curva = _curva_golpes(mono)
    fps = SR_ANALISIS / _HOP_GOLPES
    duracion = len(curva) / fps
    x = np.arange(len(curva))
    mejor = (-1.0, bpm_aprox, 0.0)
    puntajes = []
    for bpm in np.arange(bpm_aprox * 0.94, bpm_aprox * 1.06, 0.02):
        periodo = 60.0 / bpm
        fases = np.linspace(0.0, periodo, 48, endpoint=False)
        k = np.arange(int((duracion - periodo) / periodo))
        tiempos = fases[:, None] + k[None, :] * periodo           # [fases, beats]
        valores = np.interp(tiempos.ravel() * fps, x, curva).reshape(tiempos.shape).mean(axis=1)
        i = int(np.argmax(valores))
        puntajes.append(valores[i])
        if valores[i] > mejor[0]:
            mejor = (float(valores[i]), float(bpm), float(fases[i]))
    puntaje, bpm, fase = mejor
    # afinar la fase alrededor del mejor valor (pasos de 1 ms)
    periodo = 60.0 / bpm
    k = np.arange(int((duracion - periodo) / periodo))
    finas = fase + np.arange(-0.012, 0.012, 0.001)
    valores = [np.interp((f + k * periodo) * fps, x, curva).mean() for f in finas]
    fase = float(finas[int(np.argmax(valores))]) % periodo
    contraste = puntaje / (float(np.median(puntajes)) + 1e-12)
    return bpm, fase, contraste, puntaje / (float(curva.mean()) + 1e-12)


def camelot_compatibles(a, b):
    """True si dos tonalidades Camelot combinan: la misma, la vecina (±1) o su relativa (A/B)."""
    try:
        na, la, nb, lb = int(a[:-1]), a[-1], int(b[:-1]), b[-1]
    except (ValueError, IndexError):
        return False
    if na == nb:
        return True
    return la == lb and (na - nb) % 12 in (1, 11)


# ---------- análisis rápido, guardado en disco ----------

_cache = None


def _leer_cache():
    global _cache
    if _cache is None:
        try:
            with open(_RUTA_CACHE, encoding="utf-8") as f:
                _cache = json.load(f)
        except (OSError, ValueError):
            _cache = {}
    return _cache


def guardar_cache():
    if _cache is None:
        return
    os.makedirs(CARPETA_DATOS, exist_ok=True)
    temporal = _RUTA_CACHE + ".tmp"
    with open(temporal, "w", encoding="utf-8") as f:
        json.dump(_cache, f)
    os.replace(temporal, _RUTA_CACHE)


def _clave(ruta):
    """Nombre + tamaño: la misma canción se reconoce aunque el USB cambie de letra de unidad."""
    return f"{os.path.basename(ruta)}|{os.path.getsize(ruta)}"


def en_cache(ruta):
    return _leer_cache().get(_clave(ruta))


def analisis_rapido(ruta):
    """{bpm, tono, camelot} usando 60 s de la canción (desde el segundo 30). Usa la caché."""
    guardado = en_cache(ruta)
    if guardado and "dur" in guardado:
        return guardado
    meta = metadatos(ruta)
    if 0 < meta["dur"] < 60:
        raise ErrorAnalisis("Dura menos de un minuto: es muy corta para mezclarla.")
    mono = _decodificar_trozo(ruta, 30, 60)
    if len(mono) < SR_ANALISIS * 20:  # canción corta: desde el principio
        mono = _decodificar_trozo(ruta, 0, 60)
    if len(mono) < SR_ANALISIS * 20:
        raise ErrorAnalisis("El archivo es muy corto o no tiene audio.")
    bpm, _ = _bpm_y_beats(mono)
    bpm_fino, _fase, _contraste, sobre_media = _grilla_fina(mono, bpm)
    if sobre_media > 4.0:  # la grilla calza: es tempo fijo y el valor fino es más exacto
        bpm = bpm_fino
    tono, camelot = _tonalidad(mono)
    # energía: qué tan fuerte y "lleno" suena (volumen medio en dB); sirve para no saltar de una
    # canción tranquila a una muy intensa
    energia = 20.0 * np.log10(float(np.sqrt(np.mean(np.square(mono, dtype=np.float64)))) + 1e-9)
    datos = {"bpm": round(bpm, 1), "tono": tono, "camelot": camelot, "energia": round(float(energia), 1),
             "dur": round(meta["dur"], 1), "artista_tag": meta["artista"], "titulo_tag": meta["titulo"],
             "genero_tag": meta["genero"]}
    _leer_cache()[_clave(ruta)] = datos
    return datos


def listar_canciones(carpeta):
    """Rutas de todos los archivos de audio dentro de la carpeta (o USB), ordenadas."""
    rutas = []
    for raiz, carpetas, archivos in os.walk(carpeta):
        carpetas[:] = [c for c in carpetas if not c.startswith((".", "$")) and c != "System Volume Information"]
        for nombre in archivos:
            if nombre.lower().endswith(EXTENSIONES) and not nombre.startswith("._"):
                rutas.append(os.path.join(raiz, nombre))
    return sorted(rutas, key=lambda r: os.path.basename(r).lower())


# ---------- análisis completo (al cargar la canción para sonar) ----------

def analisis_completo(audio):
    """audio: [n, 2] a 44100 Hz. Devuelve un dict con:
    bpm, beats (segundos, todo el tema), fase (qué beat de cada 4 es el "uno"),
    inicio_s / fin_s (dónde hay música de verdad), estable (si el tempo es fijo)."""
    from scipy.signal import resample_poly

    mono = resample_poly(audio.mean(axis=1), 1, SR // SR_ANALISIS).astype(np.float32)
    bpm, beats = _bpm_y_beats(mono)
    if len(beats) < 16:
        raise ErrorAnalisis("No se encontró un ritmo claro en la canción.")

    # Si el tempo es fijo (música hecha con máquina), una grilla ajustada a los golpes es mucho
    # más precisa que los beats sueltos de librosa (redondeados a ~23 ms y a veces a contratiempo).
    bpm_fino, primer_beat, _contraste, sobre_media = _grilla_fina(mono, bpm)
    estable = sobre_media > 4.0
    if estable:
        bpm = bpm_fino
        beats = np.arange(primer_beat, len(mono) / SR_ANALISIS, 60.0 / bpm)

    # dónde hay música: ventanas de 1 s por encima de -30 dB respecto del nivel de la canción
    ventana = SR_ANALISIS
    n = len(mono) // ventana
    rms = np.sqrt(np.mean(np.square(mono[:n * ventana].reshape(n, ventana), dtype=np.float64), axis=1))
    activas = np.nonzero(rms > np.median(rms) * 10 ** (-30 / 20))[0]
    inicio_s = float(activas[0]) if len(activas) else 0.0
    fin_s = float(activas[-1] + 1) if len(activas) else len(mono) / SR_ANALISIS

    # el "uno" del compás: de cada 4 beats, el que más graves tiene (ahí suele caer el bombo fuerte)
    from scipy.signal import butter, sosfilt

    graves = sosfilt(butter(2, 150, btype="low", fs=SR_ANALISIS, output="sos"), mono)
    medio = int(0.05 * SR_ANALISIS)
    energia = np.array([float(np.sum(np.square(graves[max(0, int(b * SR_ANALISIS) - medio // 4):
                                                         int(b * SR_ANALISIS) + medio]))) for b in beats])
    fase = int(np.argmax([energia[p::4].mean() for p in range(4)]))

    return {"bpm": float(bpm), "beats": np.asarray(beats, dtype=np.float64), "fase": fase,
            "inicio_s": inicio_s, "fin_s": fin_s, "estable": estable}
