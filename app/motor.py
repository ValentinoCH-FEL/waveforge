"""
motor.py — puente entre Python y la librería nativa en C++.

No sabe nada de Tkinter ni de archivos WAV: solo carga la librería
compartida (filtro.dll / libfiltro.dylib / libfiltro.so según el
sistema operativo) y expone funciones para llamar cada filtro,
pasando los datos como buffers de memoria con numpy (sin copias
innecesarias entre Python y C++).
"""

import ctypes
import os
import platform

import numpy as np

# ndpointer valida en tiempo de llamada que el array sea float32
# contiguo, y le pasa a C++ el puntero real del buffer de numpy.
_FLOAT_ARRAY = np.ctypeslib.ndpointer(dtype=np.float32, flags="C_CONTIGUOUS")


def _nombre_libreria():
    sistema = platform.system()
    if sistema == "Windows":
        return "filtro.dll"
    elif sistema == "Darwin":
        return "libfiltro.dylib"
    else:
        return "libfiltro.so"


class ErrorMotor(Exception):
    """Se lanza cuando la librería nativa no se encuentra o falla."""
    pass


class MotorAudio:
    """Carga libfiltro una sola vez y expone cada filtro como un método."""

    def __init__(self, raiz_proyecto=None):
        if raiz_proyecto is None:
            raiz_proyecto = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        ruta = os.path.join(raiz_proyecto, "native", _nombre_libreria())
        if not os.path.exists(ruta):
            raise ErrorMotor(
                f"No se encontró la librería nativa en:\n{ruta}\n\n"
                "Compílala primero con el script de tu sistema operativo "
                "dentro de la carpeta native/ "
                "(build_windows.bat, build_mac.sh o build_linux.sh)."
            )

        try:
            self.lib = ctypes.CDLL(ruta)
        except OSError as error:
            raise ErrorMotor(f"No se pudo cargar la librería nativa:\n{error}")

        self._funciones = {}

    def _funcion(self, nombre):
        """Busca el filtro en la librería y le declara la firma común la primera vez que se usa.
        Así un filtro nuevo no hay que anotarlo aquí: basta con que exista en la librería."""
        fn = self._funciones.get(nombre)
        if fn is None:
            try:
                fn = getattr(self.lib, nombre)
            except AttributeError:
                raise ErrorMotor(
                    f"La librería nativa no tiene la función '{nombre}'. "
                    "¿Recompilaste native/ después de agregar el filtro?"
                )
            fn.argtypes = [_FLOAT_ARRAY, _FLOAT_ARRAY, ctypes.c_int, ctypes.c_float]
            fn.restype = None
            self._funciones[nombre] = fn
        return fn

    def aplicar_mono(self, nombre_funcion, muestras, parametro):
        """Llama un filtro de la firma común (entrada, salida, n, parametro)."""
        entrada = np.ascontiguousarray(muestras, dtype=np.float32)
        salida = np.empty_like(entrada)
        self._funcion(nombre_funcion)(entrada, salida, entrada.shape[0], np.float32(parametro))
        return salida
