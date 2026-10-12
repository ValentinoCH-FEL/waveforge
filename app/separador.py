"""
separador.py — separa una canción en pistas (voz principal, coros e instrumentos).

Cómo funciona:
  1. Demucs (de Meta, modelo de 6 pistas) separa voz, batería, bajo, guitarra,
     piano y otros instrumentos.
  2. Donde hay voz, un modelo "karaoke" (MDX-Net, paquete audio-separator) la parte
     en voz principal y coros. Los trozos sin voz se saltan (ahorra tiempo).
  3. Solo se muestran las pistas que realmente se escuchan: si la canción no tiene
     coros o piano, esas pistas no aparecen.

Para responder rápido, la canción se procesa en trozos de 30 s desde el inicio:
el primer trozo está listo en segundos y el resto se completa en segundo plano
(separar_por_partes avisa tras cada trozo). Las pistas ya separadas se guardan en
caché, así una canción repetida aparece al instante.

Cada pista es un diccionario: {id, etiqueta, color, izq, der}, con izq/der en float32.
"""

import hashlib
import importlib.util
import logging
import os
import tempfile
import threading

import numpy as np

import tema


class ErrorSeparacion(Exception):
    """No se pudo separar (falta instalar algo, o el modelo falló)."""
    pass


INSTRUCCIONES_IA = (
    "La separación con IA no está instalada.\n\n"
    "En la terminal de VS Code, con (.venv) activo, escribe:\n"
    "      pip install demucs-infer audio-separator\n\n"
    "Después cierra y abre la app. La primera vez descargará los modelos."
)

DURACION_MAXIMA_S = 10 * 60  # canciones más largas se rechazan al cargarlas

_MODELO_DEMUCS = "htdemucs_6s"
_MODELO_KARAOKE = "UVR_MDXNET_KARA_2.onnx"
_CARPETA_MODELOS = os.path.join(os.path.expanduser("~"), ".cache", "estudio_audio_modelos")
_CARPETA_CACHE = os.path.join(os.path.expanduser("~"), ".cache", "estudio_audio_cache")
_CANCIONES_EN_CACHE = 5

_SEGUNDOS_PRIMER_TROZO = 15.0  # más corto: así lo primero está listo antes
_SEGUNDOS_TROZO = 30.0
_SEGUNDOS_CONTEXTO = 2.0   # audio extra a cada lado del trozo, para que los bordes salgan limpios
_SEGUNDOS_FUNDIDO = 0.5    # fundido entre trozos vecinos (evita clics en la unión)

# Una pista por debajo de este nivel respecto de la canción se considera vacía.
_NIVEL_MINIMO_DB = -40.0
# Los coros necesitan más nivel: el modelo karaoke siempre deja algo de "resto" de la voz principal.
_NIVEL_MINIMO_COROS_DB = -30.0

_ETIQUETAS = {
    "voz_principal": ("Voz principal", tema.AZUL),
    "coros": ("Coros", "#8fd3ff"),
    "bateria": ("Batería", tema.ROJO),
    "bajo": ("Bajo", "#f2a33a"),
    "guitarra": ("Guitarra", "#b07cf0"),
    "piano": ("Piano", "#3ed6c1"),
    "otros": ("Otros instrumentos", "#7bd88f"),
}
_ORDEN = list(_ETIQUETAS)

# nombre que usa Demucs -> id de nuestra pista
_NOMBRES_DEMUCS = {"drums": "bateria", "bass": "bajo", "guitar": "guitarra", "piano": "piano", "other": "otros"}


def falta_instalar():
    """Devuelve las instrucciones de instalación si falta algún paquete, o None."""
    modulos = ("demucs_infer", "torch", "audio_separator", "onnxruntime", "soundfile")
    if all(importlib.util.find_spec(m) is not None for m in modulos):
        return None
    return INSTRUCCIONES_IA


def crear_pista(id_pista, izquierdo, derecho):
    etiqueta, color = _ETIQUETAS[id_pista]
    return {
        "id": id_pista,
        "etiqueta": etiqueta,
        "color": color,
        "izq": np.ascontiguousarray(izquierdo, dtype=np.float32),
        "der": None if derecho is None else np.ascontiguousarray(derecho, dtype=np.float32),
    }


def _rms(datos):
    return float(np.sqrt(np.mean(np.square(datos, dtype=np.float64)))) if datos.size else 0.0


def _db(valor, referencia):
    return 20.0 * np.log10(max(valor, 1e-12) / max(referencia, 1e-12))


# ---------- modelos (se cargan una sola vez por sesión) ----------

_candado_modelos = threading.Lock()
_demucs = None
_karaoke = None  # (separador, muestreo, carpeta de trabajo)


def _obtener_demucs():
    global _demucs
    import torch
    from demucs_infer import DemucsSeparator

    if _demucs is None:
        # Deja dos hilos libres: uno para la interfaz y otro para el audio en vivo.
        torch.set_num_threads(max(1, (os.cpu_count() or 3) - 2))
        dispositivo = "cuda" if torch.cuda.is_available() else "cpu"
        _demucs = DemucsSeparator(model=_MODELO_DEMUCS, device=dispositivo, overlap=0.1)
    return _demucs


def _obtener_karaoke(muestreo):
    global _karaoke
    from audio_separator.separator import Separator

    if _karaoke is None or _karaoke[1] != muestreo:
        carpeta = tempfile.mkdtemp(prefix="estudio_audio_karaoke_")
        separador = Separator(
            log_level=logging.WARNING,
            model_file_dir=_CARPETA_MODELOS,
            output_dir=carpeta,
            output_format="WAV",
            output_single_stem="Vocals",
            normalization_threshold=1.0,  # no cambiar el volumen: la pista se mezcla con las demás
            sample_rate=muestreo,
            # overlap 0.1 en vez de 0.25: ~11 % más rápido, diferencia inaudible (-36 dB).
            mdx_params={"hop_length": 1024, "segment_size": 256, "overlap": 0.1, "batch_size": 1, "enable_denoise": False},
        )
        separador.load_model(model_filename=_MODELO_KARAOKE)
        _karaoke = (separador, muestreo, carpeta)
    return _karaoke


def precargar_modelos(muestreo=44100):
    """Carga librerías y modelos por adelantado (~15 s la primera vez), para que la
    primera separación no tenga que esperarlos. Pensado para llamarse en segundo plano
    al abrir la app; si falla, la separación lo volverá a intentar y mostrará el error."""
    if falta_instalar():
        return
    try:
        with _candado_modelos:
            _obtener_demucs()
            _obtener_karaoke(muestreo)
    except Exception:
        pass


def _ajustar_largo(datos, n):
    """datos: [canales, muestras]. Recorta o rellena con ceros hasta n muestras."""
    if datos.shape[1] >= n:
        return datos[:, :n]
    relleno = np.zeros((datos.shape[0], n - datos.shape[1]), dtype=datos.dtype)
    return np.concatenate([datos, relleno], axis=1)


def _separar_demucs(onda, muestreo):
    """onda: [2, n] float32. Devuelve {id_pista o "voz": [2, n]}."""
    import torch

    with torch.no_grad():
        _mezcla, fuentes = _obtener_demucs()(torch.from_numpy(onda), sample_rate=muestreo)
    n = onda.shape[1]
    resultado = {}
    for nombre, tensor in fuentes.items():
        id_pista = "voz" if nombre == "vocals" else _NOMBRES_DEMUCS.get(nombre)
        if id_pista:
            resultado[id_pista] = _ajustar_largo(tensor.detach().cpu().numpy().astype(np.float32), n)
    return resultado


def _voz_principal(voz, muestreo):
    """voz: [2, n]. Devuelve la voz principal [2, n] (los coros son voz - principal)."""
    import soundfile as sf

    separador, _sr, carpeta = _obtener_karaoke(muestreo)
    entrada = os.path.join(carpeta, "voz.wav")
    sf.write(entrada, voz.T, muestreo, subtype="FLOAT")
    try:
        salidas = separador.separate(entrada, custom_output_names={"Vocals": "principal"})
        if not salidas:
            raise ErrorSeparacion("El modelo karaoke no devolvió la voz principal.")
        ruta = salidas[0] if os.path.isabs(salidas[0]) else os.path.join(carpeta, salidas[0])
        principal, _ = sf.read(ruta, dtype="float32", always_2d=True)
    finally:
        for archivo in os.listdir(carpeta):
            try:
                os.remove(os.path.join(carpeta, archivo))
            except OSError:
                pass

    principal = _ajustar_largo(np.ascontiguousarray(principal.T), voz.shape[1])
    if principal.shape[0] == 1:
        principal = np.repeat(principal, 2, axis=0)
    return principal


# ---------- separación por trozos ----------

def _escribir(destino, nuevo, inicio, fundido):
    """Copia nuevo ([2, k]) en destino[:, inicio:]. Las primeras `fundido` muestras se
    mezclan gradualmente con lo que ya había (la cola del trozo anterior)."""
    k = nuevo.shape[1]
    f = min(fundido, k)
    if f > 0:
        rampa = np.linspace(0.0, 1.0, f, dtype=np.float32)
        tramo = destino[:, inicio:inicio + f]
        destino[:, inicio:inicio + f] = tramo * (1.0 - rampa) + nuevo[:, :f] * rampa
    destino[:, inicio + f:inicio + k] = nuevo[:, f:]


def separar_por_partes(izquierdo, derecho, muestreo, al_avanzar=None, cancelado=None):
    """Separa la canción en trozos, del inicio al final.

    Tras cada trozo llama al_avanzar(pistas, muestras_procesadas, muestras_totales), donde
    pistas son las que ya se escuchan (sus arreglos se siguen llenando con los trozos
    siguientes). cancelado() -> True detiene el trabajo y devuelve None.
    Devuelve la lista final de pistas audibles.
    """
    if falta_instalar():
        raise ErrorSeparacion(INSTRUCCIONES_IA)
    if derecho is None:
        derecho = izquierdo  # los modelos trabajan en estéreo

    original = np.stack([izquierdo, derecho]).astype(np.float32)
    n = original.shape[1]
    primero, trozo = int(_SEGUNDOS_PRIMER_TROZO * muestreo), int(_SEGUNDOS_TROZO * muestreo)
    inicios = [0] + list(range(primero, n, trozo))
    contexto = int(_SEGUNDOS_CONTEXTO * muestreo)
    fundido = int(_SEGUNDOS_FUNDIDO * muestreo)

    salida = {id_p: np.zeros((2, n), dtype=np.float32) for id_p in _ORDEN}
    # izq/der de cada pista son vistas de `salida`: se ven llenarse trozo a trozo.
    pistas = {id_p: crear_pista(id_p, salida[id_p][0], salida[id_p][1]) for id_p in _ORDEN}

    energia = dict.fromkeys(_ORDEN, 0.0)
    energia_cancion = 0.0
    rms_cancion = _rms(original)

    with _candado_modelos:
        for k, inicio in enumerate(inicios):
            if cancelado and cancelado():
                return None
            fin = inicios[k + 1] if k + 1 < len(inicios) else n
            desde, hasta = max(0, inicio - contexto), min(n, fin + contexto)

            try:
                partes = _separar_demucs(np.ascontiguousarray(original[:, desde:hasta]), muestreo)
                voz = partes.pop("voz")
                # Solo se buscan coros donde hay voz: los tramos instrumentales se saltan.
                if _db(_rms(voz[:, inicio - desde:fin - desde]), rms_cancion) > _NIVEL_MINIMO_DB:
                    principal = _voz_principal(voz, muestreo)
                    partes["voz_principal"], partes["coros"] = principal, voz - principal
                else:
                    partes["voz_principal"], partes["coros"] = voz, np.zeros_like(voz)
            except ErrorSeparacion:
                raise
            except Exception as error:
                raise ErrorSeparacion(f"La separación con IA falló:\n{error}")

            # Se escribe el trozo más un poco de su cola, que el siguiente trozo funde con su inicio.
            hasta_escrito = min(n, fin + fundido)
            fundir = fundido if inicio > 0 else 0
            for id_p, datos in partes.items():
                nuevo = datos[:, inicio - desde:hasta_escrito - desde]
                _escribir(salida[id_p], nuevo, inicio, fundir)
                energia[id_p] += float(np.sum(np.square(nuevo[:, :fin - inicio], dtype=np.float64)))
            energia_cancion += float(np.sum(np.square(original[:, inicio:fin], dtype=np.float64)))

            if al_avanzar:
                al_avanzar(_audibles(pistas, energia, energia_cancion), fin, n)

    return _audibles(pistas, energia, energia_cancion)


def _audibles(pistas, energia, energia_cancion):
    """Las pistas que se escuchan en lo procesado hasta ahora, en orden fijo."""
    resultado = []
    for id_p in _ORDEN:
        minimo = _NIVEL_MINIMO_COROS_DB if id_p == "coros" else _NIVEL_MINIMO_DB
        if energia_cancion > 0 and 10.0 * np.log10(max(energia[id_p], 1e-12) / energia_cancion) > minimo:
            resultado.append(pistas[id_p])
    return resultado


# ---------- caché: una canción ya separada no se vuelve a procesar ----------

def clave_audio(izquierdo, derecho, muestreo):
    h = hashlib.sha1()
    h.update(f"{_MODELO_DEMUCS}|{_MODELO_KARAOKE}|{muestreo}|{len(izquierdo)}".encode())
    h.update(np.ascontiguousarray(izquierdo).tobytes())
    if derecho is not None:
        h.update(np.ascontiguousarray(derecho).tobytes())
    return h.hexdigest()


def cargar_de_cache(clave):
    """Devuelve las pistas guardadas para esta canción, o None si no están."""
    ruta = os.path.join(_CARPETA_CACHE, f"{clave}.npz")
    if not os.path.exists(ruta):
        return None
    try:
        with np.load(ruta) as datos:
            pistas = []
            for id_p in _ORDEN:
                if id_p in datos:
                    escala = float(datos[f"{id_p}_escala"])
                    estereo = datos[id_p].astype(np.float32) * np.float32(escala / 32767.0)
                    pistas.append(crear_pista(id_p, estereo[0], estereo[1]))
        os.utime(ruta)  # marca de uso reciente para la limpieza
        return pistas or None
    except Exception:
        return None  # caché dañada: se vuelve a separar


def guardar_en_cache(clave, pistas):
    """Guarda las pistas (en 16 bits para que ocupen la mitad) y conserva solo las últimas canciones.
    Una pista separada puede pasar de 1.0 (p. ej. la batería), por eso cada una guarda su escala."""
    try:
        os.makedirs(_CARPETA_CACHE, exist_ok=True)
        datos = {}
        for p in pistas:
            estereo = np.stack([p["izq"], p["der"]])
            escala = max(float(np.abs(estereo).max()), 1e-6)
            datos[p["id"]] = np.round(estereo * (32767.0 / escala)).astype(np.int16)
            datos[f"{p['id']}_escala"] = np.float64(escala)
        temporal = os.path.join(_CARPETA_CACHE, f"{clave}.tmp.npz")
        np.savez(temporal, **datos)
        os.replace(temporal, os.path.join(_CARPETA_CACHE, f"{clave}.npz"))

        archivos = sorted(
            (os.path.join(_CARPETA_CACHE, a) for a in os.listdir(_CARPETA_CACHE) if a.endswith(".npz")),
            key=os.path.getmtime, reverse=True,
        )
        for viejo in archivos[_CANCIONES_EN_CACHE:]:
            os.remove(viejo)
    except OSError:
        pass  # la caché es una ayuda: si falla, la app sigue igual
