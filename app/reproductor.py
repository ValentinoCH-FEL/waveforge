"""
reproductor.py — reproduce audio directamente desde memoria, sin abrir
el reproductor del sistema operativo.

Mezclador: reproduce la mezcla de varias pistas de principio a fin y deja
cambiar el volumen de cada una (o reemplazar las pistas) MIENTRAS suena, sin
cortar el sonido. Es lo que permite "modular en tiempo real".

Usa sounddevice (PortAudio). Si no está instalado, disponible() devuelve
False y la interfaz avisa cómo instalarlo.
"""

import numpy as np

try:
    import sounddevice as sd
    _DISPONIBLE = True
except Exception:
    _DISPONIBLE = False


class ErrorReproduccion(Exception):
    pass


def disponible():
    return _DISPONIBLE


class Mezclador:
    """Reproduce la suma de varias pistas con una ganancia cada una, de principio a fin.

    - configurar(): cambia las pistas y ganancias sin cortar el sonido ni mover la posición.
    - cambiar_ganancias(): solo el volumen de cada pista (instantáneo).
    - iniciar() sigue desde donde quedó; pausar() se detiene ahí; detener() vuelve al inicio.
    - posicion / ir_a(): dónde va la reproducción, en muestras (para la barra de reproducción).
    Al llegar al final se queda en silencio y `termino` pasa a True.
    Los cambios de ganancia se suavizan dentro de cada bloque para que no
    se oigan "clics" al mover un control.
    """

    def __init__(self):
        self._muestreo = 44100
        self._estado = ((), np.zeros(0, dtype=np.float32))  # (pistas, ganancias): se reemplaza de una vez
        self._ganancia_actual = np.zeros(0, dtype=np.float32)
        self._pos = 0
        self._stream = None
        self.nivel = (0.0, 0.0)  # pico del último bloque que sonó (izq, der), para el vúmetro

    @property
    def sonando(self):
        return self._stream is not None and self._stream.active

    @property
    def cantidad_pistas(self):
        return len(self._estado[0])

    @property
    def largo(self):
        """Muestras de las pistas configuradas (0 si no hay)."""
        pistas = self._estado[0]
        return len(pistas[0][0]) if pistas else 0

    @property
    def posicion(self):
        return min(self._pos, self.largo)

    @property
    def termino(self):
        return self.largo > 0 and self._pos >= self.largo

    def ir_a(self, muestra):
        self._pos = int(min(max(muestra, 0), self.largo))

    def configurar(self, muestreo, pistas, ganancias):
        """pistas: lista de (izquierdo, derecho_o_None), todas del mismo largo."""
        if muestreo != self._muestreo and self._stream is not None:
            self.pausar()
        self._muestreo = muestreo
        normalizadas = tuple(
            (
                np.ascontiguousarray(izq, dtype=np.float32),
                np.ascontiguousarray(izq if der is None else der, dtype=np.float32),
            )
            for izq, der in pistas
        )
        gan = np.asarray(ganancias, dtype=np.float32)
        if len(gan) != len(self._ganancia_actual):
            self._ganancia_actual = gan.copy()
        self._estado = (normalizadas, gan)

    def cambiar_ganancias(self, ganancias):
        pistas, _ = self._estado
        self._estado = (pistas, np.asarray(ganancias, dtype=np.float32))

    def iniciar(self):
        if not _DISPONIBLE:
            raise ErrorReproduccion(
                "Falta el paquete sounddevice.\nInstálalo con: pip install sounddevice"
            )
        if self.sonando:
            return
        if self.termino:
            self._pos = 0  # terminó: Reproducir empieza de nuevo
        try:
            # Búfer de ~0.25 s y bloques fijos: mientras la IA separa, el modelo karaoke retiene
            # Python hasta ~160 ms seguidos; con un búfer menor eso se oía como cortes.
            # Un cambio de volumen se sigue oyendo en menos de un cuarto de segundo.
            self._stream = sd.OutputStream(
                samplerate=self._muestreo, channels=2, dtype="float32", callback=self._callback,
                blocksize=1024, latency=0.25,
            )
            self._stream.start()
        except Exception as error:
            self._stream = None
            raise ErrorReproduccion(f"No se pudo abrir la salida de audio: {error}")

    def pausar(self):
        flujo, self._stream = self._stream, None
        if flujo is not None:
            try:
                flujo.stop()
                flujo.close()
            except Exception:
                pass

    def detener(self):
        self.pausar()
        self._pos = 0

    def _callback(self, outdata, frames, time_info, status):
        """Corre en el hilo de audio: nada pesado ni que pueda fallar sin control."""
        try:
            pistas, gan = self._estado
            outdata.fill(0.0)
            if not pistas or len(gan) != len(pistas):
                return
            n = len(pistas[0][0])
            pos = self._pos
            k = max(0, min(frames, n - pos))  # al final de la canción: silencio
            if k:
                g0 = self._ganancia_actual if len(self._ganancia_actual) == len(gan) else gan
                rampa = np.arange(k, dtype=np.float32) / k
                for i, (izq, der) in enumerate(pistas):
                    a, b = float(g0[i]), float(gan[i])
                    if a == 0.0 and b == 0.0:
                        continue
                    peso = a if a == b else a + (b - a) * rampa
                    outdata[:k, 0] += izq[pos:pos + k] * peso
                    outdata[:k, 1] += der[pos:pos + k] * peso
            self._pos = pos + k if k else max(pos, n)
            self._ganancia_actual = gan
            np.clip(outdata, -1.0, 1.0, out=outdata)
            self.nivel = (float(np.abs(outdata[:, 0]).max()), float(np.abs(outdata[:, 1]).max()))
        except Exception:
            outdata.fill(0.0)
