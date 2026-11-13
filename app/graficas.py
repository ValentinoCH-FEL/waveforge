"""
graficas.py — ondas con matplotlib, embebidas en Tkinter.

Estilo consola de DJ: una franja por pista, ondas rellenas sobre fondo negro.
Sin barra de herramientas (zoom/mover): la región que se ve se elige con los
controles de la propia app.

La figura se crea una sola vez (GraficaPistas) y en cada cambio solo se
reemplazan las ondas: rehacer la figura entera tardaba ~0.3-0.8 s y trababa
la interfaz y el audio.
"""

import tkinter as tk

import numpy as np
from matplotlib.ticker import FuncFormatter
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

import tema

# La onda se dibuja con la envolvente (mín./máx.) de ~1200 columnas: más puntos
# no se ven en pantalla y solo hacen más lento el dibujo.
_COLUMNAS = 1200

# Márgenes de las ondas dentro de la figura (fracción del ancho). La barra de reproducción
# usa los mismos, así queda alineada justo debajo de las ondas.
MARGEN_IZQ = 0.012
MARGEN_DER = 0.996


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
        self._envolventes = {}  # id(muestras) -> (muestras, t, y): una canción entera tarda en resumirse
        self._ejes = []
        self._rellenos = []
        self._textos = []

    def _crear_ejes(self, carriles):
        self._fig.clear()
        cantidad = len(carriles)
        self._ejes, self._rellenos, self._textos = [], [], []
        for i, (titulo, color, *_resto) in enumerate(carriles):
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
            left=MARGEN_IZQ, right=MARGEN_DER, top=0.985, bottom=0.10 if cantidad <= 2 else 0.12, hspace=0.07
        )

    def _envolvente(self, muestras, muestreo, inicio_s):
        """La envolvente de cada arreglo se calcula una sola vez: mover un volumen solo la reescala
        (con la canción completa, recalcularla en cada cambio trababa la interfaz)."""
        guardada = self._envolventes.get(id(muestras))
        if guardada is not None and guardada[0] is muestras:
            return guardada[1], guardada[2]
        t, y = _preparar_para_grafica(muestras, muestreo, inicio_s)
        if len(self._envolventes) > 32:
            self._envolventes.clear()
        self._envolventes[id(muestras)] = (muestras, t, y)  # guardar `muestras` evita que su id se reutilice
        return t, y

    def dibujar(self, carriles, muestreo, inicio_s=0.0, escala_comun=False):
        """carriles: lista de (titulo, color, muestras, atenuado, escala); escala multiplica la onda
        (el volumen de la pista).
        escala_comun: True = todas las franjas usan la misma altura, así se ve qué pista
        suena más fuerte; False = cada una se ajusta a su propio tamaño."""
        firma = tuple((c[0], c[1]) for c in carriles)
        if firma != self._firma:
            self._crear_ejes(carriles)
            self._firma = firma

        datos = []
        for _titulo, _color, muestras, _atenuado, escala in carriles:
            t, y = self._envolvente(muestras, muestreo, inicio_s)
            datos.append((t, y if escala == 1.0 else y * np.float32(escala)))
        picos = [float(np.max(np.abs(y))) if len(y) else 0.0 for _, y in datos]
        limite_comun = max(max(picos) * 1.1, 1e-4)

        for i, ((titulo, color, _muestras, atenuado, _escala), (t, y)) in enumerate(zip(carriles, datos)):
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


class BarraReproduccion:
    """Línea de tiempo bajo las ondas: muestra por dónde va la reproducción y, con un click
    o arrastrando, salta a otra parte. al_buscar(fraccion) recibe un valor entre 0 y 1."""

    _ALTO = 22

    def __init__(self, frame_padre, al_buscar):
        self._al_buscar = al_buscar
        self._fraccion = 0.0
        self.widget = tk.Canvas(frame_padre, height=self._ALTO, bg=tema.FONDO, highlightthickness=0, bd=0,
                                cursor="hand2")
        self.widget.bind("<Configure>", lambda _e: self._dibujar())
        self.widget.bind("<Button-1>", self._click)
        self.widget.bind("<B1-Motion>", self._click)

    def _limites(self):
        ancho = max(self.widget.winfo_width(), 1)
        return ancho * MARGEN_IZQ, ancho * MARGEN_DER

    def _click(self, evento):
        x0, x1 = self._limites()
        self._al_buscar(min(max((evento.x - x0) / max(x1 - x0, 1), 0.0), 1.0))

    def mostrar(self, fraccion):
        fraccion = min(max(fraccion, 0.0), 1.0)
        if abs(fraccion - self._fraccion) > 1e-4:
            self._fraccion = fraccion
            self._dibujar()

    def _dibujar(self):
        c = self.widget
        c.delete("all")
        x0, x1 = self._limites()
        medio = self._ALTO // 2
        x = x0 + (x1 - x0) * self._fraccion
        c.create_rectangle(x0, medio - 3, x1, medio + 3, fill=tema.ONDA_FONDO, outline=tema.BORDE)
        c.create_rectangle(x0, medio - 3, x, medio + 3, fill=tema.AZUL, outline="")
        c.create_oval(x - 7, medio - 7, x + 7, medio + 7, fill=tema.TEXTO, outline=tema.AZUL, width=2)
