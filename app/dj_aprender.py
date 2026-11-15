"""
dj_aprender.py — aprender el estilo de un DJ a partir del audio de una de sus mezclas. Sin Tkinter.

Solo con la mezcla (sin las canciones originales por separado) no se puede saber qué le hizo
el DJ a cada canción, pero sí su estilo:
  - cada cuánto cambia de canción,
  - cuánto dura cada transición (en beats),
  - qué técnica usa: corte seco, fundido, o quitar los graves durante el cambio,
  - cuánto cambia el tempo entre una canción y la siguiente.

Cómo se detecta un cambio de canción: se compara "cómo suena" el medio minuto anterior con el
medio minuto siguiente (armonía y timbre). Donde esa diferencia hace un pico, cambió la canción.
Es una estimación: un cambio fuerte dentro de una misma canción puede contarse como transición,
y una transición entre dos canciones muy parecidas puede pasar desapercibida.

El resultado es un "perfil" (JSON en ~/.waveforge/dj_perfiles/) que dj_sesion aplica al mezclar.
"""

import json
import os

import numpy as np

import dj_analisis
from dj_analisis import SR_ANALISIS

CARPETA_PERFILES = os.path.join(dj_analisis.CARPETA_DATOS, "dj_perfiles")
_PASO_S = 0.5            # resolución del análisis
_VENTANA_S = 30.0        # cuánto se mira antes y después de cada instante
_MIN_ENTRE_CAMBIOS_S = 25.0
TECNICAS = {"corte": "Corte seco", "graves": "Quitando los graves", "fundido": "Fundido"}


class ErrorAprender(Exception):
    pass


def _rasgos(mono):
    """Por cada medio segundo: armonía (croma, 12), timbre (MFCC, 12) y energía grave y total (dB)."""
    import librosa

    salto = SR_ANALISIS // 10  # 0.1 s exactos: 5 saltos = un paso de 0.5 s (con 2048 los tiempos salían corridos 8 %)
    espectro = np.abs(librosa.stft(mono, n_fft=4096, hop_length=salto)) ** 2
    croma = librosa.feature.chroma_stft(S=espectro, sr=SR_ANALISIS)
    mel = librosa.feature.melspectrogram(S=espectro, sr=SR_ANALISIS, n_mels=40)
    mfcc = librosa.feature.mfcc(S=librosa.power_to_db(mel), n_mfcc=13)[1:]
    frecuencias = librosa.fft_frequencies(sr=SR_ANALISIS, n_fft=4096)
    graves = espectro[frecuencias < 150].sum(axis=0)
    total = espectro.sum(axis=0)

    por_paso = max(1, int(round(_PASO_S * SR_ANALISIS / salto)))
    n = croma.shape[1] // por_paso

    def agrupar(x):
        return x[..., :n * por_paso].reshape(*x.shape[:-1], n, por_paso).mean(axis=-1)

    return (agrupar(croma).T, agrupar(mfcc).T,
            10 * np.log10(agrupar(graves) + 1e-10), 10 * np.log10(agrupar(total) + 1e-10))


def _distancia_antes_despues(x, ventana):
    """Para cada instante: 1 - coseno entre el promedio de la ventana anterior y el de la siguiente."""
    acumulado = np.vstack([np.zeros((1, x.shape[1])), np.cumsum(x, axis=0)])
    n = len(x)
    salida = np.zeros(n)
    for t in range(ventana, n - ventana):
        antes = acumulado[t] - acumulado[t - ventana]
        despues = acumulado[t + ventana] - acumulado[t]
        salida[t] = 1.0 - float(antes @ despues) / (np.linalg.norm(antes) * np.linalg.norm(despues) + 1e-12)
    return salida


def _bpm_en(mono, centro_s):
    """Tempo en los ~30 s alrededor de centro_s (None si no hay un ritmo claro)."""
    a = max(0, int((centro_s - 15) * SR_ANALISIS))
    trozo = mono[a:a + 30 * SR_ANALISIS]
    if len(trozo) < 12 * SR_ANALISIS:
        return None
    try:
        aproximado, _ = dj_analisis._bpm_y_beats(trozo)
        fino, _fase, _contraste, sobre_media = dj_analisis._grilla_fina(trozo, aproximado)
    except Exception:
        return None
    return float(fino if sobre_media > 4.0 else aproximado)


def analizar_mezcla(ruta, al_avanzar=None):
    """Analiza el audio de una mezcla de DJ y devuelve su perfil (dict)."""
    from scipy.signal import find_peaks

    def avance(f, texto):
        if al_avanzar:
            al_avanzar(f, texto)

    avance(0.02, "Leyendo el audio...")
    crudo = dj_analisis._ffmpeg(["-i", ruta, "-vn", "-f", "f32le", "-ac", "1", "-ar", str(SR_ANALISIS), "-"])
    mono = np.frombuffer(crudo, dtype=np.float32)
    duracion = len(mono) / SR_ANALISIS
    if duracion < 120:
        raise ErrorAprender("La mezcla dura menos de 2 minutos: no alcanza para aprender de ella.")

    avance(0.15, "Escuchando la mezcla...")
    croma, mfcc, graves_db, total_db = _rasgos(mono)
    mfcc = (mfcc - mfcc.mean(axis=0)) / (mfcc.std(axis=0) + 1e-9)
    ventana = int(_VENTANA_S / _PASO_S)

    avance(0.45, "Buscando los cambios de canción...")
    # la armonía pesa más: dentro de una canción el timbre cambia (estrofa, estribillo), el tono casi no
    novedad = 0.65 * _distancia_antes_despues(croma, ventana) + 0.35 * _distancia_antes_despues(mfcc - mfcc.min(), ventana)
    suave = np.convolve(novedad, np.ones(5) / 5, mode="same")
    validos = suave[ventana:-ventana]
    umbral = float(np.median(validos) + 0.8 * np.std(validos))
    picos, _ = find_peaks(suave, height=umbral, distance=int(_MIN_ENTRE_CAMBIOS_S / _PASO_S),
                          prominence=0.35 * float(np.std(validos)))

    transiciones = []
    for k, pico in enumerate(picos):
        avance(0.5 + 0.45 * k / max(len(picos), 1), "Midiendo cada transición...")
        # cuánto dura: en qué momento la mezcla deja de parecerse a "antes" y pasa a parecerse a "después"
        a0, a1 = max(0, pico - 2 * ventana), max(1, pico - ventana // 2)
        d0, d1 = min(len(croma) - 1, pico + ventana // 2), min(len(croma), pico + 2 * ventana)
        rasgo = np.hstack([croma, 0.3 * (mfcc - mfcc.min())])
        antes, despues = rasgo[a0:a1].mean(axis=0), rasgo[d0:d1].mean(axis=0)

        def parecido(x, y):
            return x @ y / (np.linalg.norm(x, axis=-1) * np.linalg.norm(y) + 1e-12)

        zona = slice(max(0, pico - ventana), min(len(rasgo), pico + ventana))
        local = np.vstack([np.convolve(rasgo[zona, j], np.ones(4) / 4, mode="same") for j in range(rasgo.shape[1])]).T
        curva = parecido(local, despues) - parecido(local, antes)          # negativo = suena a "antes"
        bajo, alto = np.percentile(curva, 10), np.percentile(curva, 90)
        curva = (curva - bajo) / (alto - bajo + 1e-12)                      # 0 = antes, 1 = después
        centro = pico - zona.start
        inicio = centro
        while inicio > 0 and curva[inicio] > 0.2:
            inicio -= 1
        fin = centro
        while fin < len(curva) - 1 and curva[fin] < 0.8:
            fin += 1
        inicio_s, fin_s = (zona.start + inicio) * _PASO_S, (zona.start + fin) * _PASO_S
        dur_s = max(fin_s - inicio_s, _PASO_S)

        # técnica: ¿corte?, ¿se hunden los graves durante el cambio?
        i0, i1 = int(inicio_s / _PASO_S), max(int(fin_s / _PASO_S), int(inicio_s / _PASO_S) + 1)
        relativo = graves_db - total_db
        referencia = np.median(np.concatenate([relativo[a0:a1], relativo[d0:d1]]))
        hundimiento = float(referencia - np.min(relativo[i0:i1 + 1]))
        tecnica = "corte" if dur_s <= 3.0 else ("graves" if hundimiento > 7.0 else "fundido")

        bpm_antes = _bpm_en(mono, inicio_s - 20)
        bpm_despues = _bpm_en(mono, fin_s + 20)
        bpm = bpm_antes or bpm_despues or 120.0
        transiciones.append({
            "t": round(float(pico * _PASO_S), 1), "inicio": round(float(inicio_s), 1), "fin": round(float(fin_s), 1),
            "dur_s": round(float(dur_s), 1), "beats": round(float(dur_s * bpm / 60.0), 1), "tecnica": tecnica,
            "bpm_antes": round(bpm_antes, 1) if bpm_antes else None,
            "bpm_despues": round(bpm_despues, 1) if bpm_despues else None,
        })

    avance(0.97, "Armando el perfil...")
    return {"nombre": os.path.splitext(os.path.basename(ruta))[0], "duracion_s": round(duracion, 1),
            "transiciones": transiciones, "resumen": resumir(transiciones, duracion)}


def resumir(transiciones, duracion):
    """De la lista de transiciones al estilo del DJ: los números que el motor puede aplicar."""
    if not transiciones:
        return None
    tiempos = [0.0] + [t["t"] for t in transiciones] + [duracion]
    tramos = np.diff(tiempos)
    # el primer y el último tramo suelen estar cortados por el inicio y el fin del archivo
    centrales = tramos[1:-1] if len(tramos) > 2 else tramos
    largos = [t["beats"] for t in transiciones if t["tecnica"] != "corte"]
    beats = float(np.median(largos)) if largos else 4.0
    beats_motor = min((8, 16, 32, 64), key=lambda b: abs(np.log(b / max(beats, 1.0))))
    cuenta = {k: sum(1 for t in transiciones if t["tecnica"] == k) / len(transiciones) for k in TECNICAS}
    saltos = [abs(t["bpm_despues"] / t["bpm_antes"] - 1.0) for t in transiciones
              if t["bpm_antes"] and t["bpm_despues"]]
    # un salto mayor a 20 % casi seguro es el detector contando el doble o la mitad: no se cuenta
    saltos = [s for s in saltos if s < 0.2]
    return {"transiciones": len(transiciones),
            "segundos_por_cancion": round(float(np.median(centrales)), 1),
            "beats_transicion": round(beats, 1), "beats_motor": int(beats_motor),
            "tecnicas": {k: round(v, 2) for k, v in cuenta.items()},
            "salto_tempo_medio": round(float(np.mean(saltos)), 3) if saltos else None}


def describir(perfil):
    """Texto corto para mostrar el perfil al usuario."""
    r = perfil.get("resumen")
    if not r:
        return "No se detectaron cambios de canción en esta mezcla."
    tecnicas = ", ".join(f"{TECNICAS[k].lower()} {v * 100:.0f}%" for k, v in r["tecnicas"].items() if v > 0)
    lineas = [f"{r['transiciones']} cambios de canción en {perfil['duracion_s'] / 60:.1f} minutos.",
              f"Cambia de canción cada ~{r['segundos_por_cancion']:.0f} segundos.",
              f"Las transiciones duran ~{r['beats_transicion']:.0f} beats ({r['beats_motor'] // 4} compases al aplicarlo).",
              f"Técnicas: {tecnicas}."]
    if r["salto_tempo_medio"] is not None:
        lineas.append(f"Entre canciones el tempo cambia en promedio {r['salto_tempo_medio'] * 100:.1f}%.")
    return "\n".join(lineas)


def guardar(perfil):
    os.makedirs(CARPETA_PERFILES, exist_ok=True)
    ruta = os.path.join(CARPETA_PERFILES, "".join(c if c.isalnum() or c in " -_" else "_" for c in perfil["nombre"]) + ".json")
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(perfil, f, ensure_ascii=False, indent=1)
    return ruta


def cargar_todos():
    """Todos los perfiles guardados, del más nuevo al más viejo."""
    perfiles = []
    try:
        archivos = sorted((os.path.join(CARPETA_PERFILES, a) for a in os.listdir(CARPETA_PERFILES) if a.endswith(".json")),
                          key=os.path.getmtime, reverse=True)
    except OSError:
        return perfiles
    for ruta in archivos:
        try:
            with open(ruta, encoding="utf-8") as f:
                perfiles.append(json.load(f))
        except (OSError, ValueError):
            pass
    return perfiles
