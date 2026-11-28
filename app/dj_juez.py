"""
dj_juez.py — una IA pequeña que aprende si una mezcla va a quedar en ritmo, ANTES de hacerla.
Sin Tkinter.

El problema: para mezclar A con B hay que saber dónde caen los beats de B. El detector a veces
cuenta mal el pulso (en salsa confunde 2 con 3, o 3 con 4), y entonces B entra "a destiempo".
Hay varias formas de leer el ritmo de B (su propio tempo, el de A, el doble o la mitad del de A)
y con reglas fijas no se sabe cuál es la buena.

El juez es un modelo de gradient boosting (scikit-learn) que mira 23 características de
cada opción (tempos, qué tan bien calza cada grilla con los golpes, qué tan regular es el pulso,
tonalidad, energía, contratiempo...) y predice el calce: qué tan en ritmo va a quedar. Se entrena con
mezclas armadas con la música del propio usuario, etiquetadas solas: se mide si los golpes de B
caen sobre la grilla de A. Después se reentrena con los votos 👍/👎 del usuario.

  candidatos()  -> las distintas lecturas del ritmo de B, con sus características
  nota()        -> el calce que el modelo espera para una opción
  elegir()      -> la mejor según el modelo (o según la regla de siempre si no hay modelo)
  entrenar()    -> entrena y guarda el modelo; devuelve su precisión en canciones no vistas
"""

import json
import os

import numpy as np

import dj_analisis
import dj_motor
from dj_analisis import SR

CARPETA = os.path.join(dj_analisis.CARPETA_DATOS, "juez")
RUTA_MODELO = os.path.join(CARPETA, "modelo.joblib")
RUTA_INFO = os.path.join(CARPETA, "modelo.json")
UMBRAL_EN_RITMO = 1.3  # calce de B sobre la grilla de A desde el que la mezcla "suena en ritmo"

CARACTERISTICAS = (
    "bpm_a", "bpm_b", "diferencia_tempo", "hipotesis", "paso_a", "paso_b",
    "calce_a", "calce_b", "calce_b_propia", "calce_relativo",
    "regularidad_a", "regularidad_b", "seguidos_a", "seguidos_b",
    "fuerza_uno_a", "fuerza_uno_b", "tonalidad_compatible", "energia_diferencia", "mismo_genero",
    "contratiempo_a", "contratiempo_b", "calce_final_a", "calce_inicio_b",
)

_modelo = None


# ---------- características ----------

def _vector(local_a, local_b, propia_b, hipotesis, meta_a, meta_b):
    pa, pb, diferencia = dj_motor.relacion_tempo(local_a["bpm"], local_b["bpm"])
    fila = {
        "bpm_a": local_a["bpm"], "bpm_b": local_b["bpm"], "diferencia_tempo": diferencia,
        "hipotesis": hipotesis, "paso_a": pa, "paso_b": pb,
        "calce_a": local_a.get("calce", 1.0), "calce_b": local_b.get("calce", 1.0),
        "calce_b_propia": propia_b.get("calce", 1.0),
        "calce_relativo": local_b.get("calce", 1.0) / (propia_b.get("calce", 1.0) + 1e-9),
        "regularidad_a": local_a.get("regularidad", 0.0), "regularidad_b": local_b.get("regularidad", 0.0),
        "seguidos_a": float(local_a.get("seguidos", False)), "seguidos_b": float(local_b.get("seguidos", False)),
        "fuerza_uno_a": local_a.get("fuerza_uno", 1.0), "fuerza_uno_b": local_b.get("fuerza_uno", 1.0),
        "tonalidad_compatible": float(dj_analisis.camelot_compatibles(meta_a.get("camelot") or "",
                                                                      meta_b.get("camelot") or "")),
        "energia_diferencia": abs((meta_a.get("energia") or 0.0) - (meta_b.get("energia") or 0.0)),
        "mismo_genero": float(meta_a.get("genero") == meta_b.get("genero")),
        "contratiempo_a": local_a.get("contratiempo", 1.0), "contratiempo_b": local_b.get("contratiempo", 1.0),
        "calce_final_a": local_a.get("calce_final", 1.0), "calce_inicio_b": local_b.get("calce_inicio", 1.0),
    }
    return fila


def candidatos(audio_a, ana_a, audio_b, ana_b, meta_a=None, meta_b=None, salir_s=None):
    """Mide el ritmo del final de A y del inicio de B, y arma las lecturas posibles de B:
    0 = su propio tempo; 1, 2, 0.5 = forzado al tempo de A (o su doble o su mitad).
    Devuelve (local_a, [(local_b, caracteristicas), ...]). Solo opciones que se pueden sincronizar,
    más la propia aunque no se pueda (para el corte con eco)."""
    meta_a, meta_b = meta_a or {}, meta_b or {}
    fin_a = ana_a["fin_s"] if salir_s is None else min(salir_s, ana_a["fin_s"])
    local_a = dict(ana_a)
    g = dj_analisis.grilla_local(audio_a, fin_a - 60.0, fin_a + 0.5, ana_a["bpm"])
    local_a.update(g)

    desde_b, hasta_b = ana_b["inicio_s"], ana_b["inicio_s"] + 60.0
    propia = dict(ana_b)
    propia.update(dj_analisis.grilla_local(audio_b, desde_b, hasta_b, ana_b["bpm"]))
    opciones = [(propia, _vector(local_a, propia, propia, 0.0, meta_a, meta_b))]
    vistos = [propia["bpm"]]
    for factor in (1.0, 2.0, 0.5):
        objetivo = local_a["bpm"] * factor
        if not 60.0 <= objetivo <= 200.0:
            continue
        lectura = dict(ana_b)
        lectura.update(dj_analisis.grilla_local(audio_b, desde_b, hasta_b, objetivo))
        if not dj_motor.compatibles(local_a["bpm"], lectura["bpm"]):
            continue
        if any(abs(lectura["bpm"] / v - 1.0) < 0.005 for v in vistos):  # la misma lectura otra vez
            continue
        vistos.append(lectura["bpm"])
        opciones.append((lectura, _vector(local_a, lectura, propia, factor, meta_a, meta_b)))
    return local_a, opciones


# ---------- etiqueta: ¿la mezcla quedó en ritmo? ----------

def calce_mezcla(audio_a, local_a, audio_b, local_b, plan):
    """Pone B (con su velocidad ajustada, como sonaría en la mezcla) sobre la grilla de A y mide
    si sus golpes caen en los beats: 1 = al azar, más de 1.3 = en ritmo."""
    from scipy.signal import resample_poly

    pa, pb = plan.get("paso_a", 1), plan.get("paso_b", 1)
    beats = plan["beats"]
    beats_a = local_a["beats"][plan["i0"]:plan["i0"] + beats * pa + 1:pa]
    beats_b = local_b["beats"][plan["j0"]:plan["j0"] + beats * pb + 1:pb]
    rel = beats_a - beats_a[0]
    t = np.arange(int(rel[-1] * SR))
    origen = np.interp(t / SR, rel, beats_b) * SR
    b = np.interp(origen, np.arange(len(audio_b)), audio_b.mean(axis=1))
    mono = resample_poly(b, 1, SR // dj_analisis.SR_ANALISIS).astype(np.float32)
    curva = dj_analisis._curva_golpes(mono)
    return dj_analisis._calce(curva, rel)


# ---------- modelo ----------

def _cargar():
    global _modelo
    if _modelo is None and os.path.exists(RUTA_MODELO):
        try:
            import joblib
            _modelo = joblib.load(RUTA_MODELO)
        except Exception:  # noqa: BLE001
            _modelo = False
    return _modelo or None


def nota(caracteristicas):
    """Calce que el juez espera para esta mezcla (1 = al azar, 1.3 o más = en ritmo),
    o None si todavía no hay modelo entrenado."""
    modelo = _cargar()
    if modelo is None:
        return None
    x = np.array([[caracteristicas.get(c, 1.0) for c in CARACTERISTICAS]], dtype=float)
    return float(np.exp(modelo.predict(x)[0]))


def elegir(local_a, opciones):
    """La lectura de B con mejor nota del juez. Sin modelo: la regla de siempre
    (la propia si se puede sincronizar; si no, la forzada que mejor calce)."""
    sincronizables = [o for o in opciones if dj_motor.compatibles(local_a["bpm"], o[0]["bpm"])]
    if not sincronizables:
        return opciones[0][0], None
    if _cargar() is not None:
        notas = [nota(c) for _, c in sincronizables]
        i = int(np.argmax(notas))
        return sincronizables[i][0], round(notas[i], 2)
    propia = opciones[0]
    if propia in sincronizables:
        return propia[0], None
    mejor = max(sincronizables, key=lambda o: o[1]["calce_b"])
    return (mejor[0], None) if mejor[1]["calce_relativo"] >= 0.85 else (propia[0], None)


def _como_elector(pred, x, y, parejas):
    """En cada pareja con varias opciones: ¿la elegida queda en ritmo? (juez, regla, techo)."""
    h, cr = CARACTERISTICAS.index("hipotesis"), CARACTERISTICAS.index("calce_relativo")
    cb = CARACTERISTICAS.index("calce_b")
    juez, regla, techo = [], [], []
    for p in set(parejas):
        i = np.nonzero(parejas == p)[0]
        if len(i) < 2:
            continue
        propia = [k for k in i if x[k, h] == 0]
        forzada = max(i, key=lambda k: x[k, cb])
        k_regla = propia[0] if propia else forzada
        juez.append(y[i[np.argmax(pred[i])]] >= UMBRAL_EN_RITMO)
        regla.append(y[k_regla] >= UMBRAL_EN_RITMO)
        techo.append(y[i].max() >= UMBRAL_EN_RITMO)
    return {"parejas": len(juez), "en_ritmo_juez": round(float(np.mean(juez)), 3),
            "en_ritmo_regla": round(float(np.mean(regla)), 3), "en_ritmo_techo": round(float(np.mean(techo)), 3)}


def entrenar(rutas_datos, votos=None):
    """Entrena con archivos .jsonl de ejemplos {caracteristicas, calce, cancion_a, cancion_b}.
    votos: ejemplos del usuario (👍 = en ritmo, 👎 = no), que pesan 5 veces más.
    Valida por canciones (5 grupos: el modelo nunca ve la canción B con la que se lo evalúa)
    y guarda el modelo final, entrenado con todo. Devuelve el resumen."""
    import joblib
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.model_selection import GroupKFold

    filas = []
    for ruta in rutas_datos:
        with open(ruta, encoding="utf-8") as f:
            filas += [json.loads(l) for l in f if l.strip()]
    filas = [r for r in filas if all(c in r["caracteristicas"] for c in CARACTERISTICAS)]
    x = np.array([[r["caracteristicas"][c] for c in CARACTERISTICAS] for r in filas], dtype=float)
    y = np.array([r["calce"] for r in filas], dtype=float)
    grupos = np.array([r["cancion_b"] for r in filas])
    parejas = np.array([r["cancion_a"] + "|" + r["cancion_b"] for r in filas])
    peso = np.ones(len(y))
    votos = [v for v in (votos or []) if all(c in v["caracteristicas"] for c in CARACTERISTICAS)]
    if votos:  # 👍 = como una mezcla bien calzada; 👎 = como una al azar
        x = np.vstack([x, [[v["caracteristicas"][c] for c in CARACTERISTICAS] for v in votos]])
        y = np.concatenate([y, [1.8 if v["voto"] > 0 else 1.0 for v in votos]])
        grupos = np.concatenate([grupos, [v["cancion_b"] for v in votos]])
        parejas = np.concatenate([parejas, [f"voto{i}" for i in range(len(votos))]])
        peso = np.concatenate([peso, np.full(len(votos), 5.0)])

    def nuevo():
        return HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=15,
                                             l2_regularization=1.0, random_state=0)

    pred = np.zeros(len(y))
    for tren, prueba in GroupKFold(5).split(x, y, grupos):
        m = nuevo()
        m.fit(x[tren], np.log(y[tren]), sample_weight=peso[tren])
        pred[prueba] = m.predict(x[prueba])
    resumen = {"ejemplos": int(len(y)), "votos": len(votos),
               "correlacion_validacion": round(float(np.corrcoef(pred, np.log(y))[0, 1]), 3)}
    resumen.update(_como_elector(pred, x, y, parejas))
    modelo = nuevo()
    modelo.fit(x, np.log(y), sample_weight=peso)
    os.makedirs(CARPETA, exist_ok=True)
    joblib.dump(modelo, RUTA_MODELO)
    with open(RUTA_INFO, "w", encoding="utf-8") as f:
        json.dump(resumen, f, indent=1)
    global _modelo
    _modelo = modelo
    return resumen
