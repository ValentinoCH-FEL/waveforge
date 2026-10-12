"""
audio_io.py — cargar/guardar WAV y validar el formato.

Solo trabaja con WAV PCM de 16 bits, mono o estéreo (requisitos 2 y 9).
Convierte entre enteros de 16 bits (lo que trae el archivo) y floats
en el rango [-1.0, 1.0] (lo que esperan los filtros en C++).
"""

import wave

import numpy as np


class ErrorFormatoAudio(Exception):
    """Formato de archivo no soportado (requisito 2: validar formato)."""
    pass


class InfoAudio:
    def __init__(self, canales, muestreo, ancho_muestra, cantidad_frames, ruta):
        self.canales = canales
        self.muestreo = muestreo
        self.ancho_muestra = ancho_muestra
        self.cantidad_frames = cantidad_frames
        self.ruta = ruta

    @property
    def duracion_segundos(self):
        return self.cantidad_frames / float(self.muestreo)

    def resumen(self):
        canales_txt = "Mono" if self.canales == 1 else "Estéreo"
        return (
            f"Duración: {self.duracion_segundos:.2f} s   "
            f"Canales: {canales_txt}   "
            f"Muestreo: {self.muestreo} Hz   "
            f"Tamaño: {self._tamano_legible()}"
        )

    def _tamano_legible(self):
        bytes_totales = self.cantidad_frames * self.canales * self.ancho_muestra
        for unidad in ("B", "KB", "MB", "GB"):
            if bytes_totales < 1024:
                return f"{bytes_totales:.1f} {unidad}"
            bytes_totales /= 1024
        return f"{bytes_totales:.1f} TB"


def cargar_wav(ruta, limite_mb=200):
    """Carga un WAV y devuelve (info, izquierdo, derecho).

    derecho es None si el audio es mono.
    Lanza ErrorFormatoAudio si el archivo no es PCM de 16 bits, o si
    excede limite_mb (requisito no funcional 10: validar límites antes
    de reservar memoria grande).
    """
    try:
        with wave.open(ruta, "rb") as wf:
            canales = wf.getnchannels()
            muestreo = wf.getframerate()
            ancho = wf.getsampwidth()
            n_frames = wf.getnframes()

            tamano_estimado_mb = (n_frames * canales * ancho) / (1024 * 1024)
            if tamano_estimado_mb > limite_mb:
                raise ErrorFormatoAudio(
                    f"El archivo pesa ~{tamano_estimado_mb:.1f} MB, "
                    f"por encima del límite de {limite_mb} MB."
                )

            datos_crudos = wf.readframes(n_frames)
    except wave.Error as error:
        raise ErrorFormatoAudio(f"No es un WAV válido: {error}")

    if ancho != 2:
        raise ErrorFormatoAudio(
            f"Solo se soporta WAV PCM de 16 bits (este archivo usa {ancho * 8} bits)."
        )
    if canales not in (1, 2):
        raise ErrorFormatoAudio(f"Solo se soporta mono o estéreo (este archivo tiene {canales} canales).")

    enteros = np.frombuffer(datos_crudos, dtype=np.int16)
    flotantes = enteros.astype(np.float32) / 32768.0

    info = InfoAudio(canales, muestreo, ancho, n_frames, ruta)

    if canales == 1:
        return info, flotantes, None

    izquierdo = np.ascontiguousarray(flotantes[0::2])
    derecho = np.ascontiguousarray(flotantes[1::2])
    return info, izquierdo, derecho


def guardar_wav(ruta, muestreo, izquierdo, derecho=None):
    """Guarda uno o dos canales (floats en [-1, 1]) como WAV PCM de 16 bits."""
    if derecho is None:
        entrelazado = np.ascontiguousarray(izquierdo, dtype=np.float32)
        canales = 1
    else:
        n = min(len(izquierdo), len(derecho))
        entrelazado = np.empty(n * 2, dtype=np.float32)
        entrelazado[0::2] = izquierdo[:n]
        entrelazado[1::2] = derecho[:n]
        canales = 2

    recortado = np.clip(entrelazado, -1.0, 1.0)
    enteros = (recortado * 32767.0).astype(np.int16)

    with wave.open(ruta, "wb") as wf:
        wf.setnchannels(canales)
        wf.setsampwidth(2)
        wf.setframerate(muestreo)
        wf.writeframes(enteros.tobytes())
