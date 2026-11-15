"""
identificador.py — reconocer la voz del cantante y quitar las demás (público, otras voces).

Cómo funciona:
  1. El usuario marca un tramo donde se oye SOLO al cantante.
  2. De ese tramo se saca una "huella de voz" (un vector de 256 números) con WeSpeaker
     ResNet34, un modelo de reconocimiento de personas por su voz (ONNX, licencia CC-BY-4.0,
     https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM).
  3. Se recorre la pista de voz en ventanas cortas y se compara cada una con la huella.
     Donde la voz no se parece, se baja: queda solo el cantante.

La comparación (lo caro) se hace una vez; después la "exigencia" se puede mover y la curva
de volumen se recalcula al instante.

El modelo espera el "fbank" de Kaldi (80 bandas mel, 16 kHz, ventanas de 25 ms cada 10 ms);
aquí se calcula con numpy, igual que torchaudio.compliance.kaldi.fbank (verificado).
"""

import os
import urllib.request

import numpy as np

_URL_MODELO = ("https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM/resolve/main/"
               "voxceleb_resnet34_LM.onnx")
_RUTA_MODELO = os.path.join(os.path.expanduser("~"), ".cache", "estudio_audio_modelos", "voxceleb_resnet34_LM.onnx")

SR_MODELO = 16000
VENTANA_S = 1.5       # largo de cada trozo que se compara con la huella
PASO_S = 0.25         # cada cuánto se compara (resolución de la curva de volumen)
_NIVEL_MINIMO_DB = -45.0  # ventanas más bajas que esto (respecto de la pista) no se tocan: es silencio

_sesion = None


class ErrorIdentificador(Exception):
    pass


# ---------- fbank de Kaldi en numpy ----------

def _mel(f):
    return 1127.0 * np.log(1.0 + f / 700.0)


def _banco_mel(n_fft, bandas=80, f_min=20.0, f_max=None, sr=SR_MODELO):
    """Banco de filtros mel triangulares como el de Kaldi (en escala mel, sin normalizar)."""
    f_max = f_max or sr / 2
    n_bins = n_fft // 2
    fft_mel = _mel(np.arange(n_bins) * sr / n_fft)
    bordes = np.linspace(_mel(f_min), _mel(f_max), bandas + 2)
    izq, centro, der = bordes[:-2, None], bordes[1:-1, None], bordes[2:, None]
    subida = (fft_mel[None, :] - izq) / (centro - izq)
    bajada = (der - fft_mel[None, :]) / (der - centro)
    banco = np.maximum(0.0, np.minimum(subida, bajada))
    return np.pad(banco, ((0, 0), (0, 1)))  # Kaldi ignora el bin de Nyquist


_BANCO = _banco_mel(512)
_VENTANA_POVEY = np.power(0.5 - 0.5 * np.cos(2 * np.pi * np.arange(400) / 399), 0.85)


def fbank(audio_16k):
    """audio float en [-1, 1] a 16 kHz -> [cuadros, 80] (log-mel de Kaldi, sin restar la media)."""
    x = np.asarray(audio_16k, dtype=np.float64) * 32768.0  # Kaldi trabaja en escala de 16 bits
    largo, paso = 400, 160
    if len(x) < largo:
        return np.zeros((0, 80), np.float32)
    cantidad = 1 + (len(x) - largo) // paso
    indices = np.arange(largo)[None, :] + paso * np.arange(cantidad)[:, None]
    cuadros = x[indices]
    cuadros = cuadros - cuadros.mean(axis=1, keepdims=True)                     # quitar continua
    cuadros = np.concatenate([cuadros[:, :1] - 0.97 * cuadros[:, :1],           # preénfasis
                              cuadros[:, 1:] - 0.97 * cuadros[:, :-1]], axis=1)
    cuadros *= _VENTANA_POVEY
    potencia = np.abs(np.fft.rfft(cuadros, n=512)) ** 2
    mel = potencia @ _BANCO.T
    return np.log(np.maximum(mel, np.finfo(np.float32).eps)).astype(np.float32)


# ---------- modelo ----------

def modelo_descargado():
    return os.path.exists(_RUTA_MODELO)


def _obtener_sesion():
    global _sesion
    if _sesion is None:
        import onnxruntime

        if not modelo_descargado():
            os.makedirs(os.path.dirname(_RUTA_MODELO), exist_ok=True)
            temporal = _RUTA_MODELO + ".descargando"
            try:
                urllib.request.urlretrieve(_URL_MODELO, temporal)
                os.replace(temporal, _RUTA_MODELO)
            except OSError as error:
                raise ErrorIdentificador(f"No se pudo descargar el modelo de voz (~26 MB):\n{error}")
        disponibles = onnxruntime.get_available_providers()
        proveedores = [p for p in ("CUDAExecutionProvider", "DmlExecutionProvider") if p in disponibles][:1]
        _sesion = onnxruntime.InferenceSession(_RUTA_MODELO, providers=proveedores + ["CPUExecutionProvider"])
    return _sesion


def a_16k(audio, muestreo):
    """Pasa audio (mono) de `muestreo` a 16 kHz."""
    from scipy.signal import resample_poly

    divisor = np.gcd(int(muestreo), SR_MODELO)
    return resample_poly(np.asarray(audio, dtype=np.float32), SR_MODELO // divisor, int(muestreo) // divisor)


def huellas(lista_feats):
    """Calcula la huella (vector unitario de 256) de cada matriz fbank de la lista."""
    sesion = _obtener_sesion()
    salida = []
    for i in range(0, len(lista_feats), 16):
        lote = [f - f.mean(axis=0, keepdims=True) for f in lista_feats[i:i + 16]]  # CMN por trozo
        largo = min(len(f) for f in lote)
        entrada = np.stack([f[:largo] for f in lote]).astype(np.float32)
        emb = sesion.run(None, {"feats": entrada})[0]
        salida.append(emb / np.maximum(np.linalg.norm(emb, axis=1, keepdims=True), 1e-9))
    return np.concatenate(salida) if salida else np.zeros((0, 256), np.float32)


# ---------- comparar toda la pista con la huella del cantante ----------

def analizar(voz_mono, muestreo, i0_ref, i1_ref, al_avanzar=None):
    """Compara cada ventana de la pista de voz con la huella del tramo de referencia.
    Devuelve (parecidos, activas): parecido coseno por ventana (cada PASO_S) y si la ventana
    tiene voz (las silenciosas no se tocan)."""
    voz16 = a_16k(voz_mono, muestreo)
    escala = SR_MODELO / float(muestreo)
    ref = voz16[int(i0_ref * escala):int(i1_ref * escala)]
    if len(ref) < SR_MODELO * 1.0:
        raise ErrorIdentificador("El tramo de referencia es muy corto: marca al menos 1 segundo "
                                 "(mejor 3 a 10) donde se oiga solo al cantante.")
    huella_ref = huellas([fbank(ref)])[0]

    ventana, paso = int(VENTANA_S * SR_MODELO), int(PASO_S * SR_MODELO)
    cantidad = max(1, int(np.ceil(len(voz16) / paso)))
    relleno = np.pad(voz16, (ventana // 2, ventana))
    nivel_pista = np.sqrt(np.mean(np.square(voz16, dtype=np.float64))) + 1e-12
    parecidos = np.ones(cantidad, np.float32)
    activas = np.zeros(cantidad, bool)
    pendientes, indices = [], []
    for k in range(cantidad):
        trozo = relleno[k * paso:k * paso + ventana]
        rms = np.sqrt(np.mean(np.square(trozo, dtype=np.float64)))
        if 20 * np.log10(rms / nivel_pista + 1e-12) > _NIVEL_MINIMO_DB:
            activas[k] = True
            pendientes.append(fbank(trozo))
            indices.append(k)
        if len(pendientes) == 64 or (k == cantidad - 1 and pendientes):
            embs = huellas(pendientes)
            parecidos[indices] = embs @ huella_ref
            pendientes, indices = [], []
            if al_avanzar:
                al_avanzar((k + 1) / cantidad)
    return parecidos, activas


def curva(parecidos, activas, exigencia):
    """Ganancia 0..1 por ventana. exigencia 0..1: cuánto se tiene que parecer una voz a la del
    cantante para quedarse (más alta = quita más, pero puede comerse partes del cantante)."""
    alto = 0.15 + 0.55 * exigencia     # desde aquí la voz se considera del cantante
    bajo = alto - 0.15                 # por debajo de esto se quita del todo
    g = np.clip((parecidos - bajo) / (alto - bajo), 0.0, 1.0)
    g[~activas] = 1.0
    # suavizar: que no "aletee" de una ventana a otra
    nucleo = np.array([0.25, 0.5, 0.25], np.float32)
    return np.convolve(np.pad(g, 1, mode="edge"), nucleo, mode="valid").astype(np.float32)


def ganancia_por_muestra(curva_ventanas, largo, muestreo):
    """Interpola la curva (un valor cada PASO_S) a una ganancia por muestra."""
    paso = PASO_S * muestreo
    t = np.arange(len(curva_ventanas)) * paso
    return np.interp(np.arange(largo, dtype=np.float64), t, curva_ventanas).astype(np.float32)
