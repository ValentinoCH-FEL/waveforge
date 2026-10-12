"""
reproductor.py — reproduce audio directamente desde memoria, sin abrir
el reproductor del sistema operativo.

Mezclador: reproduce en bucle la mezcla de varias pistas y deja cambiar
el volumen de cada una (o reemplazar las pistas) MIENTRAS suena, sin cortar
el sonido. Es lo que permite "modular en tiempo real".

Usa sounddevice (PortAudio). Si no está disponible, disponible() devuelve
False y la app usa el reproductor del sistema como respaldo.
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
    """Reproduce en bucle la suma de varias pistas con una ganancia cada una.

    - configurar(): cambia las pistas y ganancias sin cortar el sonido.
    - cambiar_ganancias(): solo el volumen de cada pista (instantáneo).
    - iniciar()/detener(): arrancar o parar.
    Los cambios de ganancia se suavizan dentro de cada bloque para que no
    se oigan "clics" al mover un control.
    """

    def __init__(self):
        self._muestreo = 44100
        self._estado = ((), np.zeros(0, dtype=np.float32))  # (pistas, ganancias): se reemplaza de una vez
        self._ganancia_actual = np.zeros(0, dtype=np.float32)
        self._pos = 0
        self._stream = None

    @property
    def sonando(self):
        return self._stream is not None and self._stream.active

    @property
    def cantidad_pistas(self):
        return len(self._estado[0])

    def configurar(self, muestreo, pistas, ganancias, reiniciar=False):
        """pistas: lista de (izquierdo, derecho_o_None), todas del mismo largo."""
        if muestreo != self._muestreo and self._stream is not None:
            self.detener()
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
        if reiniciar:
            self._pos = 0
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
        self._pos = 0
        try:
            # Búfer de ~0.1 s y bloques fijos: si la interfaz está ocupada un instante,
            # el audio no se entrecorta, y un cambio de volumen se sigue oyendo casi al momento.
            self._stream = sd.OutputStream(
                samplerate=self._muestreo, channels=2, dtype="float32", callback=self._callback,
                blocksize=1024, latency=0.1,
            )
            self._stream.start()
        except Exception as error:
            self._stream = None
            raise ErrorReproduccion(f"No se pudo abrir la salida de audio: {error}")

    def detener(self):
        flujo, self._stream = self._stream, None
        if flujo is not None:
            try:
                flujo.stop()
                flujo.close()
            except Exception:
                pass

    def _callback(self, outdata, frames, time_info, status):
        """Corre en el hilo de audio: nada pesado ni que pueda fallar sin control."""
        try:
            pistas, gan = self._estado
            outdata.fill(0.0)
            if not pistas or len(gan) != len(pistas):
                return
            n = len(pistas[0][0])
            g0 = self._ganancia_actual if len(self._ganancia_actual) == len(gan) else gan
            rampa = np.arange(frames, dtype=np.float32) / frames
            escritos = 0
            pos = self._pos % n
            while escritos < frames:
                k = min(frames - escritos, n - pos)
                for i, (izq, der) in enumerate(pistas):
                    a, b = float(g0[i]), float(gan[i])
                    if a == 0.0 and b == 0.0:
                        continue
                    peso = a if a == b else a + (b - a) * rampa[escritos:escritos + k]
                    outdata[escritos:escritos + k, 0] += izq[pos:pos + k] * peso
                    outdata[escritos:escritos + k, 1] += der[pos:pos + k] * peso
                escritos += k
                pos = (pos + k) % n
            self._pos = pos
            self._ganancia_actual = gan
            np.clip(outdata, -1.0, 1.0, out=outdata)
        except Exception:
            outdata.fill(0.0)
