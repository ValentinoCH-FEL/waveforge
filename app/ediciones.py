"""
ediciones.py — edición no destructiva por tramos, sin nada de Tkinter.

Cada pista guarda su audio original intacto más una lista de operaciones
(subir/bajar volumen, silenciar, fade, efecto) sobre un tramo [i0, i1).
El audio que suena es el original con las operaciones aplicadas en orden,
así deshacer/rehacer es exacto y no se pierde calidad al editar mucho.

Los bordes de cada tramo se suavizan unos milisegundos para que no se
oigan "clics" donde empieza o termina el cambio.
"""

import numpy as np

import filtros_registry
import procesamiento

_SUAVIZADO_S = 0.008   # rampa en los bordes de cada tramo
_MARGEN_FILTRO_S = 0.05  # audio extra a cada lado para que el filtro "arranque" antes del tramo

# Para la interfaz: nombre visible de cada tipo de operación
NOMBRES = {
    "ganancia": "Volumen",
    "silencio": "Silencio",
    "fade_in": "Fade in",
    "fade_out": "Fade out",
    "efecto": "Efecto",
}


def _rampa_bordes(largo, suavizado):
    """Peso 0→1 en el inicio y 1→0 al final (para mezclar el tramo editado con el original)."""
    peso = np.ones(largo, dtype=np.float32)
    k = min(suavizado, largo // 2)
    if k > 0:
        subida = np.linspace(0.0, 1.0, k, dtype=np.float32)
        peso[:k] = subida
        peso[-k:] = subida[::-1]
    return peso


def _aplicar_una(motor, canal, op, muestreo):
    """Aplica una operación a un canal (arreglo float32, se modifica en el lugar)."""
    n = len(canal)
    i0, i1 = max(0, int(op["i0"])), min(n, int(op["i1"]))
    if i1 - i0 < 2:
        return
    suave = int(_SUAVIZADO_S * muestreo)
    tramo = canal[i0:i1]
    tipo = op["tipo"]

    if tipo == "ganancia":
        factor = np.float32(10.0 ** (op["db"] / 20.0))
        peso = _rampa_bordes(i1 - i0, suave)
        tramo *= 1.0 + (factor - 1.0) * peso
    elif tipo == "silencio":
        tramo *= 1.0 - _rampa_bordes(i1 - i0, suave)
    elif tipo == "fade_in":
        tramo *= np.linspace(0.0, 1.0, i1 - i0, dtype=np.float32)
    elif tipo == "fade_out":
        tramo *= np.linspace(1.0, 0.0, i1 - i0, dtype=np.float32)
    elif tipo == "efecto":
        info = filtros_registry.obtener(op["filtro"])
        if info["funcion_nativa"] is None or motor is None:
            return
        margen = int(_MARGEN_FILTRO_S * muestreo)
        a, b = max(0, i0 - margen), min(n, i1 + margen)
        procesado, _ = procesamiento.aplicar_filtro(motor, info, op["valor"], canal[a:b].copy(), None)
        procesado = procesado[i0 - a:i1 - a]
        peso = _rampa_bordes(i1 - i0, suave)
        canal[i0:i1] = tramo + (procesado - tramo) * peso


def renderizar(motor, izquierdo, derecho, operaciones, muestreo):
    """Devuelve (izq, der) con todas las operaciones aplicadas. Si no hay, devuelve los originales
    (sin copiar). der puede ser None (mono)."""
    if not operaciones:
        return izquierdo, derecho
    izq = np.array(izquierdo, dtype=np.float32, copy=True)
    der = None if derecho is None else np.array(derecho, dtype=np.float32, copy=True)
    for op in operaciones:
        _aplicar_una(motor, izq, op, muestreo)
        if der is not None:
            _aplicar_una(motor, der, op, muestreo)
    return izq, der


def describir(op, muestreo):
    """Texto corto para mostrar una operación (ej. "Volumen −3 dB")."""
    tipo = op["tipo"]
    if tipo == "ganancia":
        return f"Volumen {op['db']:+.0f} dB"
    if tipo == "efecto":
        info = filtros_registry.obtener(op["filtro"])
        p = info["parametro"]
        valor = f" {op['valor']:.{p.get('decimales', 2)}f}{p.get('unidad', '')}" if p else ""
        return f"{info['etiqueta']}{valor}"
    return NOMBRES.get(tipo, tipo)


class Historial:
    """Pila de deshacer/rehacer. Cada acción es un dict que la interfaz sabe aplicar y revertir
    (agregar operaciones, eliminar o restaurar una pista...)."""

    def __init__(self):
        self._hechas = []
        self._deshechas = []

    def registrar(self, accion):
        self._hechas.append(accion)
        self._deshechas.clear()

    def deshacer(self):
        if not self._hechas:
            return None
        accion = self._hechas.pop()
        self._deshechas.append(accion)
        return accion

    def rehacer(self):
        if not self._deshechas:
            return None
        accion = self._deshechas.pop()
        self._hechas.append(accion)
        return accion

    @property
    def puede_deshacer(self):
        return bool(self._hechas)

    @property
    def puede_rehacer(self):
        return bool(self._deshechas)

    def limpiar(self):
        self._hechas.clear()
        self._deshechas.clear()


class Picos:
    """Resumen (mín./máx.) de una pista para dibujar su onda rápido a cualquier zoom.
    Se calcula una vez por bloques de 256 muestras; si el zoom pide más detalle que eso,
    se usan las muestras directamente (el tramo visible es corto en ese caso)."""

    BLOQUE = 256

    def __init__(self, muestras):
        self.muestras = muestras
        n = len(muestras) // self.BLOQUE * self.BLOQUE
        bloques = np.asarray(muestras[:n]).reshape(-1, self.BLOQUE)
        self.minimos = bloques.min(axis=1)
        self.maximos = bloques.max(axis=1)

    def columnas(self, i0, i1, cantidad):
        """Devuelve (mínimos, máximos) de `cantidad` columnas que cubren las muestras [i0, i1)."""
        cantidad = max(1, int(cantidad))
        i0, i1 = max(0, int(i0)), min(len(self.muestras), int(i1))
        if i1 <= i0:
            return np.zeros(cantidad, np.float32), np.zeros(cantidad, np.float32)
        por_columna = (i1 - i0) / cantidad
        if por_columna >= self.BLOQUE:
            fuente_min, fuente_max = self.minimos, self.maximos
            a, b = i0 // self.BLOQUE, max(i0 // self.BLOQUE + 1, i1 // self.BLOQUE)
            fuente_min, fuente_max = fuente_min[a:b], fuente_max[a:b]
        else:
            fuente_min = fuente_max = np.asarray(self.muestras[i0:i1])
        bordes = np.linspace(0, len(fuente_min), cantidad + 1).astype(np.int64)
        bordes[-1] = len(fuente_min)
        inicio = np.minimum(bordes[:-1], len(fuente_min) - 1)
        minimos = np.minimum.reduceat(fuente_min, inicio)
        maximos = np.maximum.reduceat(fuente_max, inicio)
        return minimos, maximos
