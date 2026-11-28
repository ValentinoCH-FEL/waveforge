"""
dj_trabajador.py — la IA pesada (Demucs y Whisper) en un PROCESO APARTE, con prioridad baja.

Por qué: Python ejecuta código de un solo hilo a la vez dentro de un proceso. Si la IA corre en
el mismo proceso que la ventana y el audio, cada clic y cada bloque de sonido esperan su turno:
la pantalla se traba y el sonido puede cortarse. En otro proceso, la IA usa los otros núcleos
sin estorbar, y Windows atiende primero a la ventana y al audio porque la IA tiene prioridad baja.

Uso: separar(audio) y palabras(voz, sr, desde_s) se llaman igual que antes; por dentro le piden
el trabajo a este proceso y esperan el resultado. Si el proceso no se puede crear, hacen el
trabajo en el proceso actual (como antes).
"""

import os
import threading
from concurrent.futures import ProcessPoolExecutor

import numpy as np

_pool = None
_candado = threading.Lock()
_fallo = False


def _iniciar_trabajador(hilos):
    """Corre dentro del proceso trabajador: prioridad baja y los hilos justos para la IA."""
    try:
        if os.name == "nt":
            import ctypes
            BELOW_NORMAL = 0x00004000
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), BELOW_NORMAL)
        else:
            os.nice(5)
    except Exception:  # noqa: BLE001
        pass
    try:
        import torch
        torch.set_num_threads(hilos)
    except Exception:  # noqa: BLE001
        pass


def _obtener_pool():
    global _pool, _fallo
    with _candado:
        if _pool is None and not _fallo:
            try:
                nucleos = max(1, (os.cpu_count() or 4) // 2)
                _pool = ProcessPoolExecutor(max_workers=1, initializer=_iniciar_trabajador,
                                            initargs=(max(1, nucleos - 1),))
            except Exception:  # noqa: BLE001
                _fallo = True
        return _pool


# ---------- lo que corre en el proceso trabajador ----------

def _separar_en_trabajador(audio):
    import dj_motor
    pistas = dj_motor.separar_local(audio)
    pistas.pop("otros", None)  # «otros» es la resta: se calcula del otro lado y viaja menos audio
    return {k: v.astype(np.float32, copy=False) for k, v in pistas.items()}


def _palabras_en_trabajador(voz, sr, desde_s):
    import dj_letra
    return dj_letra.palabras_local(voz, sr, desde_s)


def _precargar():
    import dj_letra
    import dj_motor
    dj_motor.separar_local(np.zeros((44100 * 2, 2), dtype=np.float32))  # carga Demucs
    if dj_letra.disponible():
        dj_letra._obtener()                                          # carga Whisper
    return True


# ---------- lo que llama el programa ----------

def precargar():
    """Arranca el trabajador y carga los modelos en segundo plano (al abrir el modo DJ)."""
    pool = _obtener_pool()
    if pool is not None:
        pool.submit(_precargar)


# ---------- caché en disco de pistas ya separadas ----------
# Separar es lo más caro (~20 s por tramo). Lo separado se guarda en disco en media precisión
# (float16: la mitad de espacio, inaudible) y se reutiliza la próxima vez que se pida ese tramo.

def _carpeta_pistas():
    import dj_analisis
    return os.path.join(dj_analisis.CARPETA_DATOS, "cache_pistas")


MAX_BYTES_PISTAS = 4 * 1024 ** 3  # 4 GB


def _leer_pistas(clave):
    ruta = os.path.join(_carpeta_pistas(), clave + ".npz")
    if not os.path.exists(ruta):
        return None
    try:
        with np.load(ruta) as datos:
            pistas = {k: datos[k].astype(np.float32) for k in datos.files}
        os.utime(ruta)  # recién usada: la última en borrarse
        return pistas
    except Exception:  # noqa: BLE001
        return None


def _guardar_pistas(clave, pistas):
    try:
        carpeta = _carpeta_pistas()
        os.makedirs(carpeta, exist_ok=True)
        temporal = os.path.join(carpeta, clave + ".tmp.npz")
        np.savez(temporal, **{k: v.astype(np.float16) for k, v in pistas.items()})
        os.replace(temporal, os.path.join(carpeta, clave + ".npz"))
        archivos = [os.path.join(carpeta, n) for n in os.listdir(carpeta) if n.endswith(".npz")]
        total = sum(os.path.getsize(a) for a in archivos)
        for a in sorted(archivos, key=os.path.getmtime):  # las más viejas primero
            if total <= MAX_BYTES_PISTAS:
                break
            total -= os.path.getsize(a)
            os.remove(a)
    except OSError:
        pass


def separar(audio):
    """Igual que dj_motor.separar_local, pero en el proceso trabajador y con caché en disco."""
    import dj_analisis
    clave = dj_analisis._huella_audio(audio, "pistas")
    guardadas = _leer_pistas(clave)
    if guardadas is not None:
        guardadas["otros"] = (audio - sum(guardadas.values())).astype(np.float32)
        return guardadas
    pistas = _separar_sin_cache(audio)
    _guardar_pistas(clave, {k: v for k, v in pistas.items() if k != "otros"})
    return pistas


def _separar_sin_cache(audio):
    pool = _obtener_pool()
    if pool is None:
        import dj_motor
        return dj_motor.separar_local(audio)
    try:
        pistas = pool.submit(_separar_en_trabajador, np.ascontiguousarray(audio, dtype=np.float32)).result()
    except Exception:  # noqa: BLE001  (si el trabajador se cayó, se hace acá)
        import dj_motor
        return dj_motor.separar_local(audio)
    pistas["otros"] = (audio - sum(pistas.values())).astype(np.float32)
    return pistas


def palabras(voz, sr, desde_s=0.0):
    pool = _obtener_pool()
    if pool is None:
        import dj_letra
        return dj_letra.palabras_local(voz, sr, desde_s)
    try:
        return pool.submit(_palabras_en_trabajador, np.asarray(voz, dtype=np.float32), sr, desde_s).result()
    except Exception:  # noqa: BLE001
        return []


def cerrar():
    global _pool
    with _candado:
        if _pool is not None:
            _pool.shutdown(wait=False, cancel_futures=True)
            _pool = None
