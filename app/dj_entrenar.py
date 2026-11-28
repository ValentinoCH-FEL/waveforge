"""
dj_entrenar.py — arma el dataset y entrena el juez de transiciones con la música del usuario.

Uso (desde la carpeta del proyecto, con el .venv):
    python app/dj_entrenar.py generar E:/ 1500     # 1500 parejas de canciones -> ejemplos
    python app/dj_entrenar.py entrenar             # entrena con todo lo generado + tus votos

Cada pareja A→B se elige como lo haría el DJ (mismo género y tempo parecido) o al azar, para
que el modelo vea casos buenos y malos. Para cada lectura posible del ritmo de B se arma el
plan de mezcla y se mide si B cae en ritmo sobre A. No usa la IA de separación: es rápido.
"""

import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dj_analisis  # noqa: E402
import dj_juez  # noqa: E402
import dj_motor  # noqa: E402
from dj_analisis import SR  # noqa: E402

RUTA_DATOS = os.path.join(dj_juez.CARPETA, "ejemplos.jsonl")
RUTA_VOTOS = os.path.join(dj_juez.CARPETA, "votos.jsonl")


def _ana_basico(audio, bpm):
    """Lo mínimo que piden el juez y el plan: dónde hay música y el tempo aproximado."""
    mono = audio.mean(axis=1)
    n = len(mono) // SR
    rms = np.sqrt(np.mean(np.square(mono[:n * SR].reshape(n, SR), dtype=np.float64), axis=1))
    activas = np.nonzero(rms > np.median(rms) * 10 ** (-30 / 20))[0]
    return {"bpm": float(bpm), "inicio_s": float(activas[0]) if len(activas) else 0.0,
            "fin_s": float(activas[-1] + 1) if len(activas) else len(mono) / SR}


def _una_pareja(ruta_a, meta_a, ruta_b, meta_b):
    filas = []
    audio_a, audio_b = dj_analisis.decodificar(ruta_a), dj_analisis.decodificar(ruta_b)
    ana_a, ana_b = _ana_basico(audio_a, meta_a["bpm"]), _ana_basico(audio_b, meta_b["bpm"])
    local_a, opciones = dj_juez.candidatos(audio_a, ana_a, audio_b, ana_b, meta_a, meta_b)
    rng = np.random.default_rng(0)
    for local_b, caracteristicas in opciones:
        if not dj_motor.compatibles(local_a["bpm"], local_b["bpm"]):
            continue
        try:
            plan = dj_motor.planear(local_a, local_b, rng)
            calce = dj_juez.calce_mezcla(audio_a, local_a, audio_b, local_b, plan)
        except Exception:  # noqa: BLE001
            continue
        filas.append({"cancion_a": os.path.basename(ruta_a), "cancion_b": os.path.basename(ruta_b),
                      "caracteristicas": caracteristicas, "calce": round(float(calce), 3)})
    return filas


def _trabajo(args):
    try:
        return _una_pareja(*args), None
    except Exception as error:  # noqa: BLE001
        return [], f"{os.path.basename(args[0])} → {os.path.basename(args[2])}: {error}"


def generar(carpeta, cantidad, semilla=1, procesos=10):
    rutas = dj_analisis.listar_canciones(carpeta)
    meta = {}
    for r in rutas:
        d = dj_analisis.en_cache(r)
        if d and d.get("bpm"):
            meta[r] = {"bpm": d["bpm"], "camelot": d.get("camelot"), "energia": d.get("energia"),
                       "genero": os.path.relpath(r, carpeta).split(os.sep)[0]}
    rutas = list(meta)
    if len(rutas) < 2:
        raise SystemExit("Primero analiza la música (abre el modo DJ con esa carpeta).")
    rng = np.random.default_rng(semilla)
    parejas = []
    for _ in range(cantidad):
        a = rutas[rng.integers(len(rutas))]
        if rng.random() < 0.7:  # como el DJ: mismo género, tempo parecido
            mismas = [r for r in rutas if r != a and meta[r]["genero"] == meta[a]["genero"]] or rutas
            dif = np.array([dj_motor.relacion_tempo(meta[a]["bpm"], meta[r]["bpm"])[2] for r in mismas])
            cercanas = [mismas[i] for i in np.argsort(dif)[:25]]
            b = cercanas[rng.integers(len(cercanas))]
        else:  # al azar: el modelo también tiene que ver casos difíciles
            b = rutas[rng.integers(len(rutas))]
        if b != a:
            parejas.append((a, meta[a], b, meta[b]))

    os.makedirs(dj_juez.CARPETA, exist_ok=True)
    t0, hechas, ejemplos, errores = time.time(), 0, 0, 0
    with ProcessPoolExecutor(max_workers=procesos) as pool, open(RUTA_DATOS, "a", encoding="utf-8") as salida:
        for fut in as_completed([pool.submit(_trabajo, p) for p in parejas]):
            filas, error = fut.result()
            hechas += 1
            errores += error is not None
            for fila in filas:
                salida.write(json.dumps(fila, ensure_ascii=False) + "\n")
            ejemplos += len(filas)
            if hechas % 50 == 0 or hechas == len(parejas):
                salida.flush()
                print(f"{hechas}/{len(parejas)} parejas  {ejemplos} ejemplos  {errores} errores  "
                      f"{time.time() - t0:.0f} s", flush=True)
    return ejemplos


def leer_votos():
    if not os.path.exists(RUTA_VOTOS):
        return []
    with open(RUTA_VOTOS, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def guardar_voto(caracteristicas, cancion_b, voto):
    """Lo llama la sesión cuando el usuario vota una mezcla: el próximo entrenamiento lo usa."""
    if not caracteristicas:
        return
    os.makedirs(dj_juez.CARPETA, exist_ok=True)
    with open(RUTA_VOTOS, "a", encoding="utf-8") as f:
        f.write(json.dumps({"caracteristicas": caracteristicas, "cancion_b": cancion_b, "voto": voto},
                           ensure_ascii=False) + "\n")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "generar":
        generar(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 1000,
                semilla=int(sys.argv[4]) if len(sys.argv) > 4 else 1)
    elif len(sys.argv) >= 2 and sys.argv[1] == "entrenar":
        print(json.dumps(dj_juez.entrenar(dj_juez.rutas_ejemplos(), leer_votos()), indent=1, ensure_ascii=False))
    else:
        print(__doc__)
