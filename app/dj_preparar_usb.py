"""
dj_preparar_usb.py — prepara una carpeta o USB entero para el modo DJ, de una vez y sin la ventana.

Hace lo mismo que el programa hace solo en segundo plano, pero usando todo el procesador:
  1. análisis rápido de cada canción (tempo, tonalidad, energía, huella de batería);
  2. huella de instrumentos (la IA separa 20 s de cada canción: voz, batería, bajo, guitarra,
     piano y metales). Es lo lento: ~15 s por canción repartidos en varios procesos.
Lo hecho se guarda y no se repite: si se corta, se vuelve a correr y sigue donde quedó.

Uso (desde la carpeta del proyecto, con el .venv):
    python app/dj_preparar_usb.py E:/
    python app/dj_preparar_usb.py E:/ --procesos 4
"""

import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dj_analisis  # noqa: E402


def _rapido(ruta):
    return ruta, *dj_analisis.analizar_seguro(ruta)


def _perfil(ruta):
    return ruta, *dj_analisis.perfil_seguro(ruta)


def _avance(etapa, hechas, total, inicio, errores):
    pasado = time.time() - inicio
    resta = pasado / max(hechas, 1) * (total - hechas)
    print(f"{etapa}: {hechas}/{total} · {pasado / 60:.0f} min · quedan ~{resta / 60:.0f} min · "
          f"{errores} con error", flush=True)


def preparar(carpeta, procesos):
    rutas = dj_analisis.listar_canciones(carpeta)
    cache = dj_analisis._leer_cache()
    print(f"{len(rutas)} canciones en {carpeta}", flush=True)

    # 1) análisis rápido
    faltan = [r for r in rutas if (dj_analisis.en_cache(r) or {}).get("v") != dj_analisis.VERSION_ANALISIS]
    inicio, hechas, errores = time.time(), 0, 0
    if faltan:
        with ProcessPoolExecutor(max_workers=procesos * 2) as pool:
            for futuro in as_completed([pool.submit(_rapido, r) for r in faltan]):
                ruta, datos, error = futuro.result()
                hechas += 1
                if datos:
                    viejo = cache.get(dj_analisis._clave(ruta)) or {}
                    if viejo.get("instrumentos"):
                        datos["instrumentos"] = viejo["instrumentos"]
                    cache[dj_analisis._clave(ruta)] = datos
                else:
                    errores += 1
                if hechas % 25 == 0 or hechas == len(faltan):
                    dj_analisis.guardar_cache()
                    _avance("Análisis", hechas, len(faltan), inicio, errores)

    # 2) huella de instrumentos (lo lento)
    faltan = [r for r in rutas if (dj_analisis.en_cache(r) or {}).get("bpm")
              and not (dj_analisis.en_cache(r) or {}).get("instrumentos")]
    inicio, hechas, errores = time.time(), 0, 0
    if faltan:
        print(f"Huella de instrumentos de {len(faltan)} canciones con {procesos} procesos…", flush=True)
        with ProcessPoolExecutor(max_workers=procesos) as pool:
            for futuro in as_completed([pool.submit(_perfil, r) for r in faltan]):
                ruta, perfil, error = futuro.result()
                hechas += 1
                guardado = cache.get(dj_analisis._clave(ruta))
                if perfil and guardado is not None:
                    guardado["instrumentos"] = perfil
                else:
                    errores += 1
                if hechas % 10 == 0 or hechas == len(faltan):
                    dj_analisis.guardar_cache()
                    _avance("Instrumentos", hechas, len(faltan), inicio, errores)
    dj_analisis.guardar_cache()
    listas = sum(1 for r in rutas if (dj_analisis.en_cache(r) or {}).get("instrumentos"))
    print(f"Listo: {listas} de {len(rutas)} canciones con todo preparado.", flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    nucleos = max(1, (os.cpu_count() or 4) // 2)
    procesos = int(sys.argv[sys.argv.index("--procesos") + 1]) if "--procesos" in sys.argv else max(1, nucleos - 1)
    preparar(sys.argv[1], procesos)
