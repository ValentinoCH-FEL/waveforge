"""
dj_equipo.py — reconoce la computadora del cliente y decide cómo trabajar en ella. Sin Tkinter.

Cada cliente tiene un equipo distinto, así que el modo DJ no asume nada: mira núcleos, memoria
y tarjeta gráfica, y además MIDE cuánto tarda de verdad en separar pistas con la IA. Con eso arma
un perfil:
  - procesos para preparar el USB (analizar toda la música en paralelo);
  - si las transiciones separan pistas con la IA (Demucs) o, en equipos modestos, cambian el
    bajo con un ecualizador (lo que hace un DJ con la perilla de graves: instantáneo).

La medición se guarda: solo se repite si cambia el equipo.
"""

import json
import os
import platform
import time

import numpy as np

import dj_analisis

_RUTA = os.path.join(dj_analisis.CARPETA_DATOS, "equipo.json")
SEGUNDOS_PRUEBA = 10.0         # audio que se separa para medir la velocidad
TIEMPO_MAXIMO_MEZCLA_S = 70.0  # una mezcla tiene que estar lista bastante antes de que termine la canción
_SEGUNDOS_POR_MEZCLA = 2 * 24  # Demucs separa ~24 s de cada canción por transición


def nucleos_fisicos():
    """os.cpu_count() cuenta hilos lógicos (normalmente el doble de los núcleos reales)."""
    logicos = os.cpu_count() or 2
    return max(1, logicos // 2) if logicos >= 4 else logicos


def memoria_gb():
    try:
        if platform.system() == "Windows":
            import ctypes

            class Estado(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            estado = Estado()
            estado.dwLength = ctypes.sizeof(Estado)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(estado))
            return estado.ullTotalPhys / 1024 ** 3
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3
    except (AttributeError, OSError, ValueError):
        return 8.0


def tarjeta_grafica():
    """'NVIDIA (CUDA)', 'DirectML' (cualquier GPU en Windows) o None."""
    try:
        import torch
        if torch.cuda.is_available():
            return "NVIDIA (CUDA)"
    except Exception:  # noqa: BLE001
        pass
    try:
        import onnxruntime
        if "DmlExecutionProvider" in onnxruntime.get_available_providers():
            return "DirectML"
    except Exception:  # noqa: BLE001
        pass
    return None


def _firma():
    return f"{platform.processor()}|{os.cpu_count()}|{round(memoria_gb())}"


def medir_separacion():
    """Segundos que tarda Demucs en separar SEGUNDOS_PRUEBA de audio en este equipo
    (None si la IA no está instalada). Usa ruido con ritmo: lo que importa es el tiempo."""
    import separador
    if separador.falta_instalar():
        return None
    rng = np.random.default_rng(0)
    n = int(SEGUNDOS_PRUEBA * dj_analisis.SR)
    t = np.arange(n) / dj_analisis.SR
    golpes = (np.sin(2 * np.pi * 2 * t) > 0.95).astype(np.float32)
    audio = (0.1 * rng.standard_normal((2, n)) + 0.3 * golpes).astype(np.float32)
    separador._separar_demucs(audio, dj_analisis.SR)  # la primera carga el modelo: no cuenta
    inicio = time.perf_counter()
    separador._separar_demucs(audio, dj_analisis.SR)
    return time.perf_counter() - inicio


def perfil(forzar_medicion=False, medir=True):
    """{nucleos, memoria_gb, grafica, procesos, segundos_por_segundo, separar_con_ia, nivel, detalle}."""
    guardado = None
    if not forzar_medicion:
        try:
            with open(_RUTA, encoding="utf-8") as f:
                guardado = json.load(f)
            if guardado.get("firma") != _firma() or (medir and guardado.get("segundos_por_segundo") is None):
                guardado = None
        except (OSError, ValueError):
            guardado = None
    if guardado:
        return guardado

    nucleos, ram, grafica = nucleos_fisicos(), memoria_gb(), tarjeta_grafica()
    # cada proceso de análisis usa ~0,7 GB; se deja un núcleo para la interfaz y el audio
    procesos = int(max(1, min(nucleos - 1, (ram - 2.0) / 0.7, 10)))
    segundos = medir_separacion() if medir else None
    por_segundo = segundos / SEGUNDOS_PRUEBA if segundos else None
    separar = por_segundo is not None and por_segundo * _SEGUNDOS_POR_MEZCLA <= TIEMPO_MAXIMO_MEZCLA_S
    if separar and (grafica == "NVIDIA (CUDA)" or por_segundo < 0.5):
        nivel = "Alto"
    elif separar:
        nivel = "Medio"
    else:
        nivel = "Básico"
    detalle = (f"{nucleos} núcleos · {ram:.0f} GB de RAM · " + (grafica or "sin tarjeta gráfica dedicada") +
               (f" · separa 1 s de audio en {por_segundo:.2f} s" if por_segundo else ""))
    datos = {"firma": _firma(), "nucleos": nucleos, "memoria_gb": round(ram, 1), "grafica": grafica,
             "procesos": procesos, "segundos_por_segundo": por_segundo, "separar_con_ia": separar,
             "nivel": nivel, "detalle": detalle}
    if not medir:  # perfil provisorio (sin medir): no se guarda, así la medición real se hace igual
        return datos
    try:
        os.makedirs(os.path.dirname(_RUTA), exist_ok=True)
        with open(_RUTA, "w", encoding="utf-8") as f:
            json.dump(datos, f, indent=1, ensure_ascii=False)
    except OSError:
        pass
    return datos


if __name__ == "__main__":
    print(json.dumps(perfil(forzar_medicion=True), indent=1, ensure_ascii=False))
