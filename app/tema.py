"""
tema.py — paleta y estilos oscuros tipo estudio/DJ para toda la app,
más el panel con scroll que usa la lista de filtros.

Todo el aspecto visual vive aquí (colores de Tkinter y de las gráficas),
así que para cambiar el look no hace falta tocar main.py.
"""

import tkinter as tk
from tkinter import ttk

# Paleta (inspirada en consolas de DJ: casi negro, azul y rojo)
FONDO = "#121214"
PANEL = "#1a1a1e"
PANEL_2 = "#232328"
BORDE = "#34343c"
TEXTO = "#e2e2e6"
TEXTO_SUAVE = "#8c8c97"
AZUL = "#27a9e1"        # audio original
ROJO = "#e8453c"        # audio procesado
ERROR = "#ff6b5e"
ONDA_FONDO = "#0b0b0d"
TEXTO_SOBRE_ACENTO = "#06121a"


def aplicar(root):
    """Aplica el tema oscuro a toda la ventana (llamar antes de crear widgets)."""
    estilo = ttk.Style(root)
    try:
        estilo.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(bg=FONDO)

    # --- widgets clásicos de Tk (menús, cuadros de texto, diálogos, barra de matplotlib) ---
    opciones = {
        "*Background": FONDO,
        "*Foreground": TEXTO,
        "*Entry.Background": PANEL_2,
        "*Entry.Foreground": TEXTO,
        "*Entry.insertBackground": TEXTO,
        "*Text.Background": PANEL,
        "*Text.Foreground": TEXTO,
        "*Text.insertBackground": TEXTO,
        "*Button.Background": PANEL_2,
        "*Button.Foreground": TEXTO,
        "*Button.activeBackground": BORDE,
        "*Button.activeForeground": TEXTO,
        "*Menu.Background": PANEL_2,
        "*Menu.Foreground": TEXTO,
        "*Menu.activeBackground": AZUL,
        "*Menu.activeForeground": TEXTO_SOBRE_ACENTO,
        "*selectBackground": AZUL,
        "*selectForeground": TEXTO_SOBRE_ACENTO,
        "*TCombobox*Listbox.Background": PANEL_2,
        "*TCombobox*Listbox.Foreground": TEXTO,
        "*TCombobox*Listbox.selectBackground": AZUL,
        "*TCombobox*Listbox.selectForeground": TEXTO_SOBRE_ACENTO,
    }
    for clave, valor in opciones.items():
        root.option_add(clave, valor)

    # --- widgets ttk ---
    estilo.configure(
        ".", background=FONDO, foreground=TEXTO, fieldbackground=PANEL_2,
        bordercolor=BORDE, lightcolor=BORDE, darkcolor=BORDE, troughcolor=ONDA_FONDO,
        focuscolor=FONDO, selectbackground=AZUL, selectforeground=TEXTO_SOBRE_ACENTO,
        insertcolor=TEXTO,
    )
    estilo.configure("TFrame", background=FONDO)
    estilo.configure("TLabel", background=FONDO, foreground=TEXTO)
    estilo.configure("Titulo.TLabel", font=("TkDefaultFont", 13, "bold"), foreground=TEXTO)

    estilo.configure(
        "TLabelframe", background=FONDO, bordercolor=BORDE, lightcolor=BORDE, darkcolor=BORDE, relief="solid"
    )
    estilo.configure("TLabelframe.Label", background=FONDO, foreground=AZUL, font=("TkDefaultFont", 9, "bold"))

    estilo.configure(
        "TButton", background=PANEL_2, foreground=TEXTO, bordercolor=BORDE,
        lightcolor=PANEL_2, darkcolor=PANEL_2, padding=(12, 6), relief="flat",
    )
    estilo.map(
        "TButton",
        background=[("pressed", BORDE), ("active", "#2e2e35"), ("disabled", PANEL)],
        foreground=[("disabled", TEXTO_SUAVE)],
        bordercolor=[("active", AZUL)],
    )
    estilo.configure(
        "Acento.TButton", background=AZUL, foreground=TEXTO_SOBRE_ACENTO, bordercolor=AZUL,
        lightcolor=AZUL, darkcolor=AZUL, font=("TkDefaultFont", 10, "bold"), padding=(14, 8),
    )
    estilo.map(
        "Acento.TButton",
        background=[("pressed", "#1a87b8"), ("active", "#4cbdee"), ("disabled", PANEL_2)],
        foreground=[("disabled", TEXTO_SUAVE)],
        bordercolor=[("active", "#4cbdee")],
    )

    for nombre in ("TRadiobutton", "TCheckbutton"):
        estilo.configure(
            nombre, background=FONDO, foreground=TEXTO, indicatorbackground=ONDA_FONDO,
            indicatorforeground=AZUL, upperbordercolor=BORDE, lowerbordercolor=BORDE,
        )
        estilo.map(
            nombre,
            background=[("active", FONDO)],
            foreground=[("disabled", TEXTO_SUAVE)],
            indicatorbackground=[("selected", ONDA_FONDO), ("pressed", ONDA_FONDO)],
            indicatorforeground=[("selected", AZUL)],
        )

    estilo.configure(
        "Horizontal.TScale", background=AZUL, troughcolor=ONDA_FONDO, bordercolor=BORDE,
        lightcolor=AZUL, darkcolor=AZUL,
    )
    estilo.map("Horizontal.TScale", background=[("active", "#4cbdee"), ("disabled", BORDE)])

    estilo.configure(
        "TCombobox", fieldbackground=PANEL_2, background=PANEL_2, foreground=TEXTO,
        arrowcolor=TEXTO, bordercolor=BORDE, lightcolor=PANEL_2, darkcolor=PANEL_2,
        selectbackground=PANEL_2, selectforeground=TEXTO, padding=4,
    )
    estilo.map(
        "TCombobox",
        fieldbackground=[("readonly", PANEL_2)],
        foreground=[("readonly", TEXTO)],
        background=[("active", "#2e2e35")],
        arrowcolor=[("disabled", TEXTO_SUAVE)],
    )

    estilo.configure(
        "Vertical.TScrollbar", background=PANEL_2, troughcolor=ONDA_FONDO, bordercolor=BORDE,
        arrowcolor=TEXTO, lightcolor=PANEL_2, darkcolor=PANEL_2,
    )
    estilo.map("Vertical.TScrollbar", background=[("active", BORDE)])

    # Botones de encendido/apagado (M = silenciar, S = solo) y barra de progreso
    base_toggle = dict(
        background=PANEL_2, foreground=TEXTO, bordercolor=BORDE, lightcolor=PANEL_2,
        darkcolor=PANEL_2, padding=(4, 2), relief="flat", anchor="center",
    )
    for nombre, color_activo, texto_activo in (
        ("Mute.Toolbutton", ROJO, "#ffffff"),
        ("Solo.Toolbutton", "#f2a33a", TEXTO_SOBRE_ACENTO),
    ):
        estilo.configure(nombre, **base_toggle)
        estilo.map(
            nombre,
            background=[("selected", color_activo), ("active", "#2e2e35")],
            foreground=[("selected", texto_activo)],
        )
    estilo.configure(
        "Horizontal.TProgressbar", background=AZUL, troughcolor=ONDA_FONDO, bordercolor=BORDE,
        lightcolor=AZUL, darkcolor=AZUL,
    )

    estilo.configure("TSeparator", background=BORDE)
    estilo.configure(
        "Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXTO,
        bordercolor=BORDE, rowheight=24,
    )
    estilo.configure("Treeview.Heading", background=PANEL_2, foreground=TEXTO, relief="flat")
    estilo.map("Treeview", background=[("selected", AZUL)], foreground=[("selected", TEXTO_SOBRE_ACENTO)])


class PanelDesplazable(ttk.Frame):
    """Marco con scroll vertical (incluye la rueda del mouse).
    Los widgets se agregan dentro de `.interior`."""

    def __init__(self, padre, ancho=250):
        super().__init__(padre)
        self._canvas = tk.Canvas(self, width=ancho, bg=FONDO, highlightthickness=0, bd=0)
        self._barra = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self.interior = ttk.Frame(self._canvas)
        self._ventana = self._canvas.create_window((0, 0), window=self.interior, anchor="nw")
        self._canvas.configure(yscrollcommand=self._barra.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        self._barra.pack(side="right", fill="y")

        self.interior.bind("<Configure>", lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>", lambda e: self._canvas.itemconfigure(self._ventana, width=e.width))
        self.bind("<Enter>", self._activar_rueda)
        self.bind("<Leave>", self._desactivar_rueda)

    def _activar_rueda(self, _evento):
        self._canvas.bind_all("<MouseWheel>", self._rueda)
        self._canvas.bind_all("<Button-4>", lambda e: self._canvas.yview_scroll(-1, "units"))
        self._canvas.bind_all("<Button-5>", lambda e: self._canvas.yview_scroll(1, "units"))

    def _desactivar_rueda(self, _evento):
        for secuencia in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self._canvas.unbind_all(secuencia)

    def _rueda(self, evento):
        self._canvas.yview_scroll(-1 if evento.delta > 0 else 1, "units")
