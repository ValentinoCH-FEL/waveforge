"""
widgets.py — controles propios con animación, dibujados en un Canvas de Tk:

  - BotonAnimado: botón redondeado que se ilumina suave al pasar el mouse, se "hunde"
    al hacer click y puede ser alternable (encendido/apagado, ej. M y S de cada pista).
    Muestra una ayuda (tooltip) si el mouse se queda encima.
  - MedidorVolumen: vúmetro estéreo animado (verde/amarillo/rojo) con marca de pico.
"""

import math
import tkinter as tk
import tkinter.font as tkfont

import tema

_PASO_MS = 15        # cada cuánto avanza una animación
_PASOS_HOVER = 7     # cuadros que tarda en encenderse/apagarse el brillo


def _a_rgb(color):
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def mezclar_color(a, b, t):
    """Color entre a y b (t de 0 a 1)."""
    ra, ga, ba = _a_rgb(a)
    rb, gb, bb = _a_rgb(b)
    return "#%02x%02x%02x" % (round(ra + (rb - ra) * t), round(ga + (gb - ga) * t), round(ba + (bb - ba) * t))


ESTILOS = {
    #            fondo         hover        texto            borde
    "normal": (tema.PANEL_2, "#34343f", tema.TEXTO, tema.BORDE),
    "acento": (tema.AZUL, "#5cc6f2", tema.TEXTO_SOBRE_ACENTO, tema.AZUL),
    "suave": (tema.FONDO, tema.PANEL_2, tema.TEXTO_SUAVE, tema.FONDO),
    "peligro": (tema.PANEL_2, "#4a2326", tema.ERROR, tema.BORDE),
}


class Ayuda:
    """Tooltip: aparece si el mouse se queda quieto sobre el widget."""

    def __init__(self, widget, texto):
        self.widget, self.texto, self._id, self._ventana = widget, texto, None, None
        widget.bind("<Enter>", self._programar, add="+")
        widget.bind("<Leave>", self._ocultar, add="+")
        widget.bind("<ButtonPress>", self._ocultar, add="+")

    def _programar(self, _e=None):
        self._cancelar()
        self._id = self.widget.after(550, self._mostrar)

    def _cancelar(self):
        if self._id:
            self.widget.after_cancel(self._id)
            self._id = None

    def _mostrar(self):
        if not self.texto or not self.widget.winfo_exists():
            return
        x = self.widget.winfo_rootx() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self._ventana = tk.Toplevel(self.widget)
        self._ventana.wm_overrideredirect(True)
        self._ventana.wm_geometry(f"+{x}+{y}")
        tk.Label(self._ventana, text=self.texto, bg=tema.PANEL_2, fg=tema.TEXTO, bd=1, relief="solid",
                 padx=8, pady=4, font=("TkDefaultFont", 9), justify="left").pack()

    def _ocultar(self, _e=None):
        self._cancelar()
        if self._ventana is not None:
            self._ventana.destroy()
            self._ventana = None


class BotonAnimado(tk.Canvas):
    def __init__(self, padre, texto, comando=None, estilo="normal", alternable=False, variable=None,
                 color_encendido=None, ayuda=None, ancho=None, alto=30, fuente=("TkDefaultFont", 9, "bold")):
        self._fondo_padre = _fondo_de(padre)
        super().__init__(padre, height=alto, bg=self._fondo_padre, highlightthickness=0, bd=0, cursor="hand2")
        self.texto, self.comando, self.alternable, self.variable = texto, comando, alternable, variable
        self.colores = ESTILOS[estilo]
        self.color_encendido = color_encendido or tema.AZUL
        self.fuente = fuente
        self._alto = alto
        self._hover = 0.0          # 0 = normal, 1 = iluminado (se anima)
        self._objetivo = 0.0
        self._presionado = False
        self._habilitado = True
        self._animando = False
        ancho = ancho or (tkfont.Font(font=fuente).measure(texto) + 26)
        self.config(width=ancho)
        self.bind("<Enter>", lambda e: self._animar_a(1.0))
        self.bind("<Leave>", lambda e: (self._animar_a(0.0), self._soltar_sin_click()))
        self.bind("<ButtonPress-1>", self._presionar)
        self.bind("<ButtonRelease-1>", self._soltar)
        self.bind("<Configure>", lambda e: self._dibujar())
        if variable is not None:
            variable.trace_add("write", lambda *a: self._dibujar())
        if ayuda:
            Ayuda(self, ayuda)

    # --- estado ---

    def habilitar(self, si=True):
        self._habilitado = si
        self.config(cursor="hand2" if si else "arrow")
        self._dibujar()

    def cambiar_texto(self, texto):
        if texto != self.texto:
            self.texto = texto
            self._dibujar()

    @property
    def encendido(self):
        return bool(self.variable.get()) if self.variable is not None else False

    # --- animación ---

    def _animar_a(self, objetivo):
        self._objetivo = objetivo if self._habilitado else 0.0
        if not self._animando:
            self._animando = True
            self._paso()

    def _paso(self):
        if not self.winfo_exists():
            return
        delta = 1.0 / _PASOS_HOVER
        if abs(self._hover - self._objetivo) <= delta:
            self._hover = self._objetivo
            self._animando = False
        else:
            self._hover += delta if self._objetivo > self._hover else -delta
        self._dibujar()
        if self._animando:
            self.after(_PASO_MS, self._paso)

    def _presionar(self, _e):
        if self._habilitado:
            self._presionado = True
            self._dibujar()

    def _soltar_sin_click(self):
        if self._presionado:
            self._presionado = False
            self._dibujar()

    def _soltar(self, evento):
        if not self._presionado:
            return
        self._presionado = False
        dentro = 0 <= evento.x <= self.winfo_width() and 0 <= evento.y <= self.winfo_height()
        if dentro and self._habilitado:
            if self.alternable and self.variable is not None:
                self.variable.set(not self.variable.get())
            self._destello()
            if self.comando:
                self.comando()
        self._dibujar()

    def _destello(self):
        """Un brillo corto al hacer click (se apaga solo)."""
        self._hover = 1.6
        self._animar_a(1.0 if self._objetivo else 0.0)

    # --- dibujo ---

    def _dibujar(self):
        if not self.winfo_exists():
            return
        self.delete("all")
        ancho, alto = self.winfo_width(), self._alto
        if ancho <= 2:
            ancho = int(self.cget("width"))
        fondo, hover, color_texto, borde = self.colores
        if self.alternable and self.encendido:
            fondo, hover, color_texto, borde = (self.color_encendido, mezclar_color(self.color_encendido, "#ffffff", 0.25),
                                                tema.TEXTO_SOBRE_ACENTO, self.color_encendido)
        if not self._habilitado:
            fondo, color_texto = tema.PANEL, tema.BORDE
        t = min(self._hover, 1.0)
        relleno = mezclar_color(fondo, hover, t)
        if self._hover > 1.0:  # destello del click
            relleno = mezclar_color(relleno, "#ffffff", min(self._hover - 1.0, 0.6) * 0.5)
        hundido = 1 if self._presionado else 0
        _rect_redondeado(self, 1 + hundido, 1 + hundido, ancho - 1 - hundido, alto - 1 - hundido, 7,
                         fill=relleno, outline=mezclar_color(borde, tema.AZUL, t * 0.6))
        self.create_text(ancho / 2, alto / 2 + hundido, text=self.texto, fill=color_texto, font=self.fuente)


class MedidorVolumen(tk.Canvas):
    """Vúmetro estéreo: barras con caída suave y marca de pico que baja despacio."""

    _DB_MIN = -48.0

    def __init__(self, padre, ancho=170, alto=24):
        super().__init__(padre, width=ancho, height=alto, bg=_fondo_de(padre), highlightthickness=0, bd=0)
        self._ancho, self._alto = ancho, alto
        self._nivel = [0.0, 0.0]   # 0..1 ya en escala de dB
        self._pico = [0.0, 0.0]
        self._espera_pico = [0, 0]
        Ayuda(self, "Volumen de salida (izquierdo / derecho). Rojo = cerca de saturar.")

    def _a_fraccion(self, amplitud):
        if amplitud <= 1e-6:
            return 0.0
        db = 20 * math.log10(min(amplitud, 1.0))
        return max(0.0, min(1.0, (db - self._DB_MIN) / -self._DB_MIN))

    def actualizar(self, izquierdo, derecho):
        for i, amp in enumerate((izquierdo, derecho)):
            objetivo = self._a_fraccion(amp)
            # sube al instante, baja suave (como un vúmetro de verdad)
            self._nivel[i] = objetivo if objetivo > self._nivel[i] else self._nivel[i] * 0.86 + objetivo * 0.14
            if self._nivel[i] >= self._pico[i]:
                self._pico[i], self._espera_pico[i] = self._nivel[i], 25
            elif self._espera_pico[i] > 0:
                self._espera_pico[i] -= 1
            else:
                self._pico[i] = max(0.0, self._pico[i] - 0.012)
        self._dibujar()

    def _dibujar(self):
        self.delete("all")
        alto_barra = (self._alto - 6) / 2
        for i in range(2):
            y0 = 2 + i * (alto_barra + 2)
            self.create_rectangle(0, y0, self._ancho, y0 + alto_barra, fill=tema.ONDA_FONDO, outline=tema.BORDE)
            largo = self._ancho * self._nivel[i]
            # tres tramos de color: verde hasta -12 dB, amarillo hasta -3 dB, rojo arriba
            for desde, hasta, color in ((0.0, 0.75, "#3ecf6e"), (0.75, 0.9375, "#f2c53a"), (0.9375, 1.0, tema.ROJO)):
                a, b = self._ancho * desde, min(largo, self._ancho * hasta)
                if b > a:
                    self.create_rectangle(a + 1, y0 + 1, b, y0 + alto_barra - 1, fill=color, outline="")
            xp = self._ancho * self._pico[i]
            if xp > 2:
                self.create_line(xp, y0 + 1, xp, y0 + alto_barra - 1, fill=tema.TEXTO, width=2)


def _fondo_de(widget):
    """Color de fondo del contenedor (ttk no lo expone igual que tk)."""
    try:
        return widget.cget("background") or tema.FONDO
    except tk.TclError:
        return tema.FONDO


def _rect_redondeado(canvas, x0, y0, x1, y1, r, **opciones):
    r = max(0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    puntos = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1,
              x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return canvas.create_polygon(puntos, smooth=True, **opciones)
