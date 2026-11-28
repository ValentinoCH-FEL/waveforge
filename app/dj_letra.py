"""
dj_letra.py — sabe dónde está cantando la voz, palabra por palabra. Sin Tkinter.

Usa Whisper (OpenAI, código abierto, licencia MIT) sobre la PISTA DE VOZ ya separada por la IA:
sin la música, reconoce mucho mejor dónde hay palabras. Devuelve cada palabra con su segundo de
inicio y de fin. Con eso el copiloto evita cortar un verso a la mitad y prefiere cambiar de
canción justo cuando termina uno.

Límites: con canto (y más en salsa en vivo, con coros y público) se equivoca más que con voz
hablada. Para saber DÓNDE hay palabras funciona bien; las palabras exactas, solo a veces.
El modelo «base» (~140 MB) se descarga la primera vez y corre en el procesador.
"""

import threading

import numpy as np

MODELO = "base"
SR_WHISPER = 16000
_modelo = None
_candado = threading.Lock()


def disponible():
    """Si Whisper está instalado (sin cargarlo: cargarlo es pesado y se hace en el trabajador)."""
    import importlib.util
    return importlib.util.find_spec("whisper") is not None


def _obtener():
    global _modelo
    if _modelo is None:
        import whisper
        _modelo = whisper.load_model(MODELO, device="cpu")
    return _modelo


def palabras(voz, sr, desde_s=0.0):
    """Igual que palabras_local, pero Whisper corre en el proceso trabajador (no traba la ventana)."""
    if not disponible():
        return []
    import dj_trabajador
    return dj_trabajador.palabras(voz, sr, desde_s)


def palabras_local(voz, sr, desde_s=0.0):
    """voz: pista de voz separada ([n] o [n, 2]) a sr Hz. Devuelve [(inicio_s, fin_s, palabra)] en
    segundos absolutos (sumando desde_s). Lista vacía si no hay voz o Whisper no está."""
    if not disponible():
        return []
    from scipy.signal import resample_poly

    mono = np.asarray(voz, dtype=np.float32)
    if mono.ndim == 2:
        mono = mono.mean(axis=1)
    if float(np.sqrt(np.mean(np.square(mono)))) < 1e-3:  # tramo instrumental: nada que escuchar
        return []
    divisor = np.gcd(int(sr), SR_WHISPER)
    mono = resample_poly(mono, SR_WHISPER // divisor, int(sr) // divisor).astype(np.float32)
    pico = float(np.abs(mono).max()) + 1e-9
    mono = mono / pico * 0.9
    with _candado:  # un solo Whisper a la vez
        resultado = _obtener().transcribe(mono, word_timestamps=True, fp16=False, verbose=None,
                                          condition_on_previous_text=False, no_speech_threshold=0.5)
    salida = []
    for segmento in resultado.get("segments", []):
        if segmento.get("no_speech_prob", 0.0) > 0.7:
            continue
        for p in segmento.get("words", []):
            texto = p.get("word", "").strip()
            if texto and p["end"] > p["start"]:
                salida.append((desde_s + float(p["start"]), desde_s + float(p["end"]), texto))
    return salida


def _arreglos(lista):
    """Inicios y fines de las palabras como arreglos (rápido: se llama muchas veces por segundo)."""
    if not lista:
        return np.zeros(0), np.zeros(0)
    a = np.asarray([(p[0], p[1]) for p in lista], dtype=np.float64)
    return a[:, 0], a[:, 1]


def cantando(lista, desde_s, hasta_s, arreglos=None):
    """Fracción del tramo [desde_s, hasta_s] en que hay palabras."""
    if not lista or hasta_s <= desde_s:
        return 0.0
    ini, fin = arreglos or _arreglos(lista)
    tapado = np.clip(np.minimum(fin, hasta_s) - np.maximum(ini, desde_s), 0.0, None).sum()
    return float(min(1.0, tapado / (hasta_s - desde_s)))


def fin_de_verso(lista, t, hueco=1.0, arreglos=None):
    """True si en t la voz está en un respiro: la última palabra terminó antes de t y la próxima
    empieza al menos `hueco` segundos después de esa (termina un verso, no a mitad de frase)."""
    if not lista:
        return True
    ini, fin = arreglos or _arreglos(lista)
    antes = fin[fin <= t + 0.2]
    if len(antes) == 0:
        return True
    ultima_fin = float(antes.max())
    despues = ini[ini > t - 0.2]
    proxima = float(despues.min()) if len(despues) else t + 99.0
    return proxima - ultima_fin >= hueco and t - ultima_fin < 6.0


def ultimas_palabras(lista, t, cuantas=4):
    """Las últimas palabras cantadas antes de t (para mostrar dónde termina el verso)."""
    antes = [p[2] for p in lista if p[1] <= t + 0.2]
    return " ".join(antes[-cuantas:])
