"""
graficas.py — ondas con matplotlib, embebidas en Tkinter.

Estilo consola de DJ: una franja por pista, ondas rellenas sobre fondo negro.
Sin barra de herramientas (zoom/mover): la región que se ve se elige con los
controles de la propia app.

La figura se crea una sola vez (GraficaPistas) y en cada cambio solo se
reemplazan las ondas: rehacer la figura entera tardaba ~0.3-0.8 s y trababa
la interfaz y el audio.
"""

import numpy as np
from matplotlib.ticker import FuncFormatter
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

import tema

# La onda se dibuja con la envolvente (mín./máx.) de ~1200 columnas: más puntos
# no se ven en pantalla y solo hacen más lento el dibujo.
_COLUMNAS = 1200


def _preparar_para_grafica(muestras, muestreo, inicio_s=0.0, max_puntos=2 * _COLUMNAS):
    """Reduce la cantidad de puntos a graficar para que la UI no se congele
    con audios largos. Usa la envolvente (mínimo y máximo de cada bloque), así
    los picos no se pierden aunque se muestre el audio completo.
    No afecta al audio real, solo a la vista. inicio_s desplaza el eje de
    tiempo (para regiones que no empiezan en 0)."""
    n = len(muestras)
    if n <= max_puntos:
        tiempo = inicio_s + np.arange(n) / muestreo
        return tiempo, muestras

    bloque = max(1, n // (max_puntos // 2))
    usable = (n // bloque) * bloque
    bloques = np.asarray(muestras[:usable]).reshape(-1, bloque)
    minimos = bloques.min(axis=1)
    maximos = bloques.max(axis=1)

    y = np.empty(minimos.size * 2, dtype=np.float32)
    y[0::2] = minimos
    y[1::2] = maximos
    centros = inicio_s + (np.arange(minimos.size) * bloque + bloque / 2) / muestreo
    return np.repeat(centros, 2), y


def _formato_tiempo(segundos, _pos):
    segundos = int(max(segundos, 0))
    return f"{segundos // 60}:{segundos % 60:02d}"


class GraficaPistas:
    """Gráfica de franjas (una por pista) que se actualiza sin recrearse."""

    def __init__(self, frame_padre):
        self._fig = Figure(figsize=(7.5, 3.0), dpi=100, facecolor=tema.FONDO)
        self._canvas = FigureCanvasTkAgg(self._fig, master=frame_padre)
        self.widget = self._canvas.get_tk_widget()
        self._firma = None    # (título, color) de cada franja: si cambia, se rehacen los ejes
        self._ejes = []
        self._rellenos = []
        self._textos = []

    def _crear_ejes(self, carriles):
        self._fig.clear()
        cantidad = len(carriles)
        self._ejes, self._rellenos, self._textos = [], [], []
        for i, (titulo, color, _muestras, _atenuado) in enumerate(carriles):
            eje = self._fig.add_subplot(cantidad, 1, i + 1, sharex=self._ejes[0] if self._ejes else None)
            eje.set_facecolor(tema.ONDA_FONDO)
            for borde in eje.spines.values():
                borde.set_color(tema.BORDE)
            eje.tick_params(colors=tema.TEXTO_SUAVE, labelsize=8, length=3)
            eje.set_yticks([])
            eje.grid(axis="x", color=tema.BORDE, linewidth=0.5, alpha=0.6)
            eje.axhline(0, color=tema.BORDE, linewidth=0.6)
            if i < cantidad - 1:
                eje.tick_params(labelbottom=False)
            texto = eje.text(
                0.008, 0.90, titulo, transform=eje.transAxes, color=color, fontsize=8,
                fontweight="bold", va="top", ha="left",
            )
            self._ejes.append(eje)
            self._rellenos.append(None)
            self._textos.append(texto)

        # El eje de tiempo se muestra como m:ss (sin etiqueta, para no recortarse en ventanas bajas).
        self._ejes[-1].xaxis.set_major_formatter(FuncFormatter(_formato_tiempo))
        self._fig.subplots_adjust(
            left=0.012, right=0.996, top=0.985, bottom=0.10 if cantidad <= 2 else 0.12, hspace=0.07
        )

    def dibujar(self, carriles, muestreo, inicio_s=0.0, escala_comun=False):
        """carriles: lista de (titulo, color, muestras, atenuado).
        escala_comun: True = todas las franjas usan la misma altura, así se ve qué pista
        suena más fuerte; False = cada una se ajusta a su propio tamaño."""
        firma = tuple((c[0], c[1]) for c in carriles)
        if firma != self._firma:
            self._crear_ejes(carriles)
            self._firma = firma

        datos = [_preparar_para_grafica(c[2], muestreo, inicio_s) for c in carriles]
        picos = [float(np.max(np.abs(y))) if len(y) else 0.0 for _, y in datos]
        limite_comun = max(max(picos) * 1.1, 1e-4)

        for i, ((titulo, color, _muestras, atenuado), (t, y)) in enumerate(zip(carriles, datos)):
            eje = self._ejes[i]
            if self._rellenos[i] is not None:
                self._rellenos[i].remove()
            self._rellenos[i] = eje.fill_between(
                t, y, 0, color=color, linewidth=0, alpha=0.28 if atenuado else 0.95
            )
            limite = limite_comun if escala_comun else max(picos[i] * 1.1, 1e-4)
            eje.set_ylim(-limite, limite)
            self._textos[i].set_text(titulo + ("  ·  silenciada" if atenuado else ""))
            self._textos[i].set_alpha(0.55 if atenuado else 1.0)

        if len(datos[0][0]):
            self._ejes[0].set_xlim(datos[0][0][0], datos[0][0][-1])
        self._canvas.draw_idle()
