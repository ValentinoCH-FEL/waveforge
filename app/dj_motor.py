"""
dj_motor.py — arma la transición entre dos canciones usando sus pistas (stems). Sin Tkinter.

Idea: en vez de bajar una canción y subir la otra, se intercambian pistas compás a compás
(el bajo de B reemplaza al de A, la batería de A se corta, la voz de A queda sobre el ritmo
de B...). Para gastar poco, la IA separa SOLO el tramo de la transición de cada canción
(~16-20 s), no las canciones enteras.

  1. planear(): elige en qué beat de A empieza la mezcla (cerca del final, en un "uno" de
     compás), en qué beat entra B y con qué receta.
  2. preparar(): separa los dos tramos, alinea B beat por beat con A (cambiando su velocidad,
     como el pitch de un plato: sin artefactos) y aplica la receta.
  3. Después de la mezcla, B vuelve suave a su velocidad original.
"""

import numpy as np

import separador
from dj_analisis import SR

BEATS = 32                    # largo de la transición: 8 compases
MAX_DIFERENCIA_TEMPO = 0.10   # hasta ±10 % se igualan los tempos; más que eso, corte con eco
SEGUNDOS_VUELTA = 8.0         # lo que tarda B en volver a su velocidad original
_MARGEN = SR                  # audio extra a cada lado del tramo que se separa (contexto para la IA)
_E = 0.004                    # ancho de un corte "seco" (fracción de la transición, ~60 ms)

STEMS = ("voz", "bateria", "bajo", "otros")


def _corte(f): return [(0, 1), (f, 1), (f + _E, 0), (1, 0)]
def _entra(f): return [(0, 0), (f, 0), (f + _E, 1), (1, 1)]
def _baja(f0, f1): return [(0, 1), (f0, 1), (f1, 0), (1, 0)]
def _sube(f0, f1): return [(0, 0), (f0, 0), (f1, 1), (1, 1)]


# Cada receta dice, para cada pista de A y de B, su volumen a lo largo de la transición (0 a 1).
# Reglas comunes: A empieza completa y termina en silencio; B empieza en silencio y termina
# completa; las dos voces nunca suenan juntas; los cortes secos caen en inicio de compás.
RECETAS = {
    "cambio_de_bajo": {
        "nombre": "Cambio de bajo",
        "A": {"bateria": _corte(.5), "bajo": _corte(.5), "voz": _baja(.25, .5), "otros": _baja(.5, 1)},
        "B": {"bateria": _entra(.5), "bajo": _entra(.5), "voz": _entra(.75), "otros": _sube(0, .5)},
    },
    "sin_bateria": {
        "nombre": "Quitar la batería",
        "A": {"bateria": _corte(.25), "bajo": _baja(.5, .75), "voz": _baja(.5, .75), "otros": _baja(.5, 1)},
        "B": {"bateria": _entra(.5), "bajo": _entra(.75), "voz": _sube(.875, 1), "otros": _sube(.25, .75)},
    },
    "voz_sobre_ritmo": {
        "nombre": "Voz de A sobre el ritmo de B",
        "A": {"bateria": _corte(.25), "bajo": _corte(.25), "voz": _baja(.75, 1), "otros": _baja(0, .25)},
        "B": {"bateria": _entra(.25), "bajo": _entra(.25), "voz": _sube(.95, 1), "otros": _sube(.25, .5)},
    },
    "eco": {
        "nombre": "Corte con eco",
        "eco": .5,
        "A": {s: _corte(.5) for s in STEMS},
        "B": {"bateria": _entra(.25), "bajo": _entra(.5), "voz": _entra(.5), "otros": _entra(.5)},
    },
    # sin igualar tempos (canciones de velocidades muy distintas): A se va con eco y B entra de golpe
    "eco_libre": {
        "nombre": "Corte con eco (tempos distintos)",
        "eco": .5,
        "A": {s: _corte(.5) for s in STEMS},
        "B": {s: _entra(.5) for s in STEMS},
    },
}
SINCRONIZADAS = ("cambio_de_bajo", "sin_bateria", "voz_sobre_ritmo", "eco")


class ErrorMezcla(Exception):
    pass


def compatibles(bpm_a, bpm_b):
    return abs(bpm_b / bpm_a - 1.0) <= MAX_DIFERENCIA_TEMPO


def planear(ana_a, ana_b, rng, pesos=None, beats=BEATS, salir_s=None):
    """Decide dónde y cómo mezclar. Devuelve un plan (dict) o lanza ErrorMezcla si no hay lugar.
    beats: largo de la transición. salir_s: segundo de A donde debe terminar la mezcla (estilo
    aprendido de un DJ que cambia rápido); por defecto, cerca del final de la canción."""
    beats_a, beats_b = ana_a["beats"], ana_b["beats"]
    # A: la mezcla termina ~1 s antes de que se acabe la música, y empieza en un "uno" de compás
    limite = ana_a["fin_s"] - 1.0 if salir_s is None else min(salir_s, ana_a["fin_s"] - 1.0)
    i_fin = int(np.searchsorted(beats_a, limite)) - 1
    i0 = i_fin - beats
    i0 -= (i0 - ana_a["fase"]) % 4
    # B: entra en su primer "uno" con música
    j0 = int(np.searchsorted(beats_b, ana_b["inicio_s"]))
    j0 += (ana_b["fase"] - j0) % 4
    if i0 < 8 or j0 + beats + 4 >= len(beats_b):
        raise ErrorMezcla("Alguna de las dos canciones es muy corta para mezclarla.")

    sincronizada = compatibles(ana_a["bpm"], ana_b["bpm"])
    if sincronizada:
        p = np.array([(pesos or {}).get(r, 0.5) for r in SINCRONIZADAS], dtype=float)  # 0.5 = sin votos aún
        receta = str(rng.choice(SINCRONIZADAS, p=p / p.sum()))
    else:
        receta = "eco_libre"
    return {"i0": i0, "j0": j0, "receta": receta, "sincronizada": sincronizada, "beats": beats,
            "a0": int(round(beats_a[i0] * SR)), "a1": int(round(beats_a[i0 + beats] * SR))}


def separar_tramo(audio):
    """audio: [n, 2]. Devuelve {voz, bateria, bajo, otros: [n, 2]} con Demucs. "otros" es todo lo
    que no es voz, batería ni bajo, así las cuatro pistas suman exactamente el audio original."""
    if separador.falta_instalar():
        raise ErrorMezcla("La separación con IA no está instalada.")
    with separador._candado_modelos:
        partes = separador._separar_demucs(np.ascontiguousarray(audio.T), SR)
    stems = {s: np.ascontiguousarray(partes[s].T) for s in ("voz", "bateria", "bajo")}
    stems["otros"] = audio - stems["voz"] - stems["bateria"] - stems["bajo"]
    return stems


def _muestrear(pista, posiciones):
    """Lee pista ([n, 2]) en posiciones fraccionarias (cambio de velocidad). Fuera de rango: silencio."""
    x = np.arange(len(pista))
    return np.stack([np.interp(posiciones, x, pista[:, c], left=0.0, right=0.0) for c in (0, 1)], axis=1)


def _envolvente(puntos, fraccion):
    f, g = zip(*puntos)
    return np.interp(fraccion, f, g).astype(np.float32)[:, None]


def _limitar(x):
    """Recorte suave: dos canciones sumadas pueden pasarse de 1.0."""
    a = np.abs(x)
    return np.where(a > 0.8, np.sign(x) * (0.8 + 0.2 * np.tanh((a - 0.8) / 0.2)), x).astype(np.float32)


def preparar(audio_a, ana_a, audio_b, ana_b, plan, cancelado=None):
    """Construye el audio de la transición según el plan.
    Devuelve {mezcla: [m, 2], b_continua: muestra de B donde sigue sola a velocidad normal}."""
    a0, a1 = plan["a0"], plan["a1"]
    BEATS = plan.get("beats", 32)  # largo de esta transición (tapa el valor por defecto del módulo)
    n = a1 - a0
    receta = RECETAS[plan["receta"]]
    beats_a = ana_a["beats"][plan["i0"]:plan["i0"] + BEATS + 1] * SR - a0      # beats de A, relativos
    beats_b = ana_b["beats"][plan["j0"]:plan["j0"] + BEATS + 1] * SR           # beats de B, absolutos
    salida = np.arange(n, dtype=np.float64)
    fraccion = np.interp(salida, beats_a, np.linspace(0.0, 1.0, BEATS + 1))    # 0..1, medido en beats

    # --- de qué parte de B sale cada instante de la mezcla ---
    if plan["sincronizada"]:
        origen_b = np.interp(salida, beats_a, beats_b)                          # beat a beat, calzados
        v_final = (beats_b[-1] - beats_b[-2]) / (beats_a[-1] - beats_a[-2])     # velocidad de B al terminar
        fin_b = beats_b[-1]
    else:
        medio = int(np.interp(0.5, np.linspace(0, 1, BEATS + 1), beats_a))      # B arranca en el corte
        origen_b = beats_b[0] + (salida - medio)
        v_final, fin_b = 1.0, beats_b[0] + (n - medio)

    # --- separar solo los dos tramos ---
    b_desde = max(0, int(origen_b[0]) - _MARGEN)
    b_hasta = min(len(audio_b), int(fin_b) + _MARGEN)
    a_desde, a_hasta = max(0, a0 - _MARGEN), min(len(audio_a), a1 + _MARGEN)
    stems_a = separar_tramo(audio_a[a_desde:a_hasta])
    if cancelado and cancelado():
        return None
    stems_b = separar_tramo(audio_b[b_desde:b_hasta])
    if cancelado and cancelado():
        return None

    # --- aplicar la receta ---
    mezcla = np.zeros((n, 2), dtype=np.float32)
    for s in STEMS:
        mezcla += stems_a[s][a0 - a_desde:a0 - a_desde + n] * _envolvente(receta["A"][s], fraccion)
        mezcla += (_muestrear(stems_b[s], origen_b - b_desde) * _envolvente(receta["B"][s], fraccion)).astype(np.float32)

    if "eco" in receta:  # el último beat de A antes del corte se repite, cada vez más bajo
        corte = int(np.interp(receta["eco"], np.linspace(0, 1, BEATS + 1), beats_a))
        largo = int(beats_a[1] - beats_a[0])
        trozo = audio_a[a0 + corte - largo:a0 + corte].copy()
        borde = min(256, largo // 4)
        trozo[:borde] *= np.linspace(0, 1, borde, dtype=np.float32)[:, None]
        trozo[-borde:] *= np.linspace(1, 0, borde, dtype=np.float32)[:, None]
        for k in range(1, 7):
            inicio = corte + (k - 1) * largo
            if inicio + largo > n:
                break
            mezcla[inicio:inicio + largo] += trozo * np.float32(0.55 ** k)

    # --- B vuelve de a poco a su velocidad original ---
    cola = np.zeros((0, 2), dtype=np.float32)
    b_continua = int(round(fin_b))
    if abs(v_final - 1.0) > 0.002:
        m = int(SEGUNDOS_VUELTA * SR)
        velocidad = np.linspace(v_final, 1.0, m)
        posiciones = fin_b + np.cumsum(velocidad) - velocidad[0]
        cola = _muestrear(audio_b[b_continua - 2:int(posiciones[-1]) + 4], posiciones - (b_continua - 2)).astype(np.float32)
        b_continua = int(round(posiciones[-1] + 1.0))

    # El limitador actúa solo donde suenan las dos canciones; en los extremos la mezcla es igual
    # a la canción sola, así no hay ningún salto al empalmar con ella.
    peso = np.clip(np.minimum(fraccion, 1.0 - fraccion) / 0.06, 0.0, 1.0).astype(np.float32)[:, None]
    mezcla = _limitar(mezcla) * peso + mezcla * (1.0 - peso)

    return {"mezcla": np.concatenate([mezcla, cola]), "b_continua": b_continua,
            "origen_b": origen_b, "fraccion": fraccion}
