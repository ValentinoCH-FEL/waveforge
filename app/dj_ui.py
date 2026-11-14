"""
dj_ui.py — pestaña "DJ": elegir una carpeta o USB con música y dejar que el DJ mezcle solo.

  - Izquierda: la biblioteca, con el BPM y la tonalidad (Camelot) de cada canción a medida que
    se analizan. Doble click en una canción: empezar por ella.
  - Derecha: qué suena, por dónde va, cuál viene, qué tipo de mezcla hizo, y los botones
    👍 / 👎 para calificar cada transición (con eso el DJ va aprendiendo tu gusto).
"""

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import dj_sesion
import tema
from widgets import BotonAnimado, MedidorVolumen

MS_CUADRO = 100


def _tiempo(segundos):
    segundos = int(max(0, segundos))
    return f"{segundos // 60}:{segundos % 60:02d}"


class PestanaDJ(ttk.Frame):
    def __init__(self, padre, registrar_log, antes_de_sonar=None):
        super().__init__(padre)
        self._registrar_log = registrar_log
        self._antes_de_sonar = antes_de_sonar or (lambda: None)   # p. ej. pausar el editor Moldear
        self.sesion = dj_sesion.SesionDJ(al_evento=self._evento_de_fondo)
        self._filas = {}           # índice de la biblioteca -> id de la fila en la tabla
        self._ultimo_indice = None
        self._construir()
        self._tick()

    # ---------- interfaz ----------

    def _construir(self):
        cabecera = ttk.Frame(self)
        cabecera.pack(fill="x", padx=20, pady=(16, 8))
        ttk.Label(cabecera, text="Modo DJ", style="Titulo.TLabel").pack(side="left")
        ttk.Label(cabecera, foreground=tema.TEXTO_SUAVE,
                  text="   Elige una carpeta o un USB con música: el DJ elige la siguiente canción y arma cada "
                       "mezcla con sus pistas.").pack(side="left")

        cuerpo = ttk.Frame(self)
        cuerpo.pack(fill="both", expand=True, padx=20, pady=(0, 16))

        # --- biblioteca ---
        izquierda = ttk.LabelFrame(cuerpo, text="Biblioteca")
        izquierda.pack(side="left", fill="both", expand=True, padx=(0, 14))
        barra = ttk.Frame(izquierda)
        barra.pack(fill="x", padx=10, pady=(10, 6))
        BotonAnimado(barra, "📂  Elegir carpeta o USB...", self._elegir_carpeta, estilo="acento", alto=32,
                     ayuda="Una carpeta de tu PC o la unidad de un USB con música").pack(side="left")
        self.etiqueta_biblioteca = ttk.Label(barra, text="Todavía no elegiste ninguna carpeta.",
                                             foreground=tema.TEXTO_SUAVE)
        self.etiqueta_biblioteca.pack(side="left", padx=12)
        marco_tabla = ttk.Frame(izquierda)
        marco_tabla.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.tabla = ttk.Treeview(marco_tabla, columns=("titulo", "bpm", "tono"), show="headings", selectmode="browse")
        for columna, texto, ancho, ancla in (("titulo", "Canción", 360, "w"), ("bpm", "BPM", 70, "e"),
                                             ("tono", "Tono", 90, "center")):
            self.tabla.heading(columna, text=texto)
            self.tabla.column(columna, width=ancho, anchor=ancla, stretch=columna == "titulo")
        desliz = ttk.Scrollbar(marco_tabla, orient="vertical", command=self.tabla.yview)
        self.tabla.configure(yscrollcommand=desliz.set)
        desliz.pack(side="right", fill="y")
        self.tabla.pack(side="left", fill="both", expand=True)
        self.tabla.tag_configure("sonando", foreground=tema.AZUL)
        self.tabla.tag_configure("error", foreground=tema.ERROR)
        self.tabla.bind("<Double-Button-1>", lambda e: self._empezar(desde_seleccion=True))

        # --- cabina ---
        derecha = ttk.LabelFrame(cuerpo, text="Cabina")
        derecha.pack(side="left", fill="y")
        caja = tk.Frame(derecha, bg=tema.FONDO, width=430)
        caja.pack(fill="both", expand=True, padx=14, pady=12)
        caja.pack_propagate(False)

        tk.Label(caja, text="AHORA SUENA", bg=tema.FONDO, fg=tema.TEXTO_SUAVE,
                 font=("TkDefaultFont", 8, "bold")).pack(anchor="w")
        self.etiqueta_titulo = tk.Label(caja, text="—", bg=tema.FONDO, fg=tema.TEXTO, anchor="w", justify="left",
                                        wraplength=400, font=("TkDefaultFont", 13, "bold"))
        self.etiqueta_titulo.pack(fill="x", pady=(2, 2))
        self.etiqueta_detalle = tk.Label(caja, text="", bg=tema.FONDO, fg=tema.AZUL, anchor="w")
        self.etiqueta_detalle.pack(fill="x")
        self.progreso = ttk.Progressbar(caja, mode="determinate", maximum=1000)
        self.progreso.pack(fill="x", pady=(10, 2))
        fila_tiempo = tk.Frame(caja, bg=tema.FONDO)
        fila_tiempo.pack(fill="x")
        self.etiqueta_tiempo = tk.Label(fila_tiempo, text="", bg=tema.FONDO, fg=tema.TEXTO_SUAVE, font=("Consolas", 10))
        self.etiqueta_tiempo.pack(side="left")
        self.medidor = MedidorVolumen(fila_tiempo, ancho=150, alto=18)
        self.medidor.pack(side="right")

        tk.Label(caja, text="SIGUIENTE", bg=tema.FONDO, fg=tema.TEXTO_SUAVE,
                 font=("TkDefaultFont", 8, "bold")).pack(anchor="w", pady=(18, 0))
        self.etiqueta_siguiente = tk.Label(caja, text="—", bg=tema.FONDO, fg=tema.TEXTO, anchor="w", justify="left",
                                           wraplength=400, font=("TkDefaultFont", 10, "bold"))
        self.etiqueta_siguiente.pack(fill="x", pady=(2, 2))
        self.etiqueta_mensaje = tk.Label(caja, text="", bg=tema.FONDO, fg=tema.TEXTO_SUAVE, anchor="w",
                                         justify="left", wraplength=400)
        self.etiqueta_mensaje.pack(fill="x")

        controles = tk.Frame(caja, bg=tema.FONDO)
        controles.pack(fill="x", pady=(18, 0))
        self.boton_empezar = BotonAnimado(controles, "▶  Empezar", self._empezar, estilo="acento", alto=34,
                                          ayuda="Empieza por la canción elegida en la lista (o por una al azar)")
        self.boton_empezar.pack(side="left")
        self.boton_mezclar = BotonAnimado(controles, "⏭  Mezclar ya", self._mezclar_ya, alto=34,
                                          ayuda="Salta a 5 segundos antes de la mezcla (cuando ya está lista)")
        self.boton_mezclar.pack(side="left", padx=8)
        BotonAnimado(controles, "■  Detener", self._detener, alto=34).pack(side="left")

        tk.Label(caja, text="¿QUÉ TAL LA ÚLTIMA MEZCLA?", bg=tema.FONDO, fg=tema.TEXTO_SUAVE,
                 font=("TkDefaultFont", 8, "bold")).pack(anchor="w", pady=(22, 4))
        votos = tk.Frame(caja, bg=tema.FONDO)
        votos.pack(fill="x")
        self.boton_bien = BotonAnimado(votos, "👍  Me gustó", lambda: self._votar(1), alto=34,
                                       ayuda="El DJ usará más seguido este tipo de mezcla")
        self.boton_bien.pack(side="left")
        self.boton_mal = BotonAnimado(votos, "👎  No me gustó", lambda: self._votar(-1), alto=34, estilo="peligro",
                                      ayuda="El DJ usará menos este tipo de mezcla")
        self.boton_mal.pack(side="left", padx=8)
        self.etiqueta_voto = tk.Label(caja, text="", bg=tema.FONDO, fg=tema.TEXTO_SUAVE, anchor="w", justify="left",
                                      wraplength=400)
        self.etiqueta_voto.pack(fill="x", pady=(6, 0))

        tk.Label(caja, bg=tema.FONDO, fg=tema.TEXTO_SUAVE, anchor="w", justify="left", wraplength=400,
                 font=("TkDefaultFont", 8),
                 text="La IA separa solo los segundos de cada mezcla, mientras suena la canción anterior, y cada "
                      "canción se descarta de la memoria al terminar. Las canciones con tempos a más de ±10 % "
                      "se unen con un corte con eco.").pack(side="bottom", fill="x")

    # ---------- biblioteca ----------

    def _elegir_carpeta(self):
        carpeta = filedialog.askdirectory(title="Carpeta o USB con música")
        if carpeta:
            self.abrir_carpeta(carpeta)

    def abrir_carpeta(self, carpeta):
        self.sesion.detener()
        cantidad = self.sesion.cargar_carpeta(carpeta)
        self.tabla.delete(*self.tabla.get_children())
        self._filas = {}
        for i, cancion in enumerate(self.sesion.biblioteca):
            self._filas[i] = self.tabla.insert("", "end", values=self._valores(cancion))
        self._registrar_log(f"DJ: carpeta {carpeta} ({cantidad} canciones)")
        self._actualizar_biblioteca()
        if cantidad == 0:
            messagebox.showinfo("Modo DJ", "No se encontraron archivos de audio en esa carpeta.")

    @staticmethod
    def _valores(cancion):
        if cancion["error"]:
            return (cancion["titulo"], "—", "no se pudo leer")
        if cancion["bpm"] is None:
            return (cancion["titulo"], "…", "…")
        return (cancion["titulo"], f"{cancion['bpm']:.0f}", f"{cancion['camelot']} · {cancion['tono']}")

    def _actualizar_biblioteca(self):
        biblioteca = self.sesion.biblioteca
        for i, fila in self._filas.items():
            if i < len(biblioteca) and self.tabla.exists(fila):
                cancion = biblioteca[i]
                valores = self._valores(cancion)
                if tuple(self.tabla.item(fila, "values")) != tuple(str(v) for v in valores):
                    self.tabla.item(fila, values=valores)
                etiquetas = ("error",) if cancion["error"] else (("sonando",) if i == self._ultimo_indice else ())
                self.tabla.item(fila, tags=etiquetas)
        if biblioteca:
            hechas = self.sesion.analizadas()
            texto = (f"{len(biblioteca)} canciones" if hechas >= len(biblioteca)
                     else f"{len(biblioteca)} canciones · analizando {hechas} de {len(biblioteca)}...")
            self.etiqueta_biblioteca.config(text=texto)

    # ---------- controles ----------

    def _empezar(self, desde_seleccion=False):
        if not self.sesion.biblioteca:
            messagebox.showinfo("Modo DJ", "Primero elige una carpeta o un USB con música.")
            return
        indice = None
        elegida = self.tabla.selection()
        if elegida:
            indice = next((i for i, fila in self._filas.items() if fila == elegida[0]), None)
        elif desde_seleccion:
            return
        self._antes_de_sonar()
        try:
            self.sesion.empezar(indice)
        except RuntimeError as error:
            messagebox.showerror("Modo DJ", str(error))
            return
        self.etiqueta_voto.config(text="")

    def _detener(self):
        self.sesion.detener()

    def _mezclar_ya(self):
        if not self.sesion.mezclar_ya():
            self.etiqueta_voto.config(text="La mezcla todavía no está lista.")

    def _votar(self, voto):
        cual = self.sesion.calificar(voto)
        if cual is None:
            self.etiqueta_voto.config(text="Todavía no sonó ninguna mezcla nueva para calificar.")
            return
        self.etiqueta_voto.config(text=f"{'👍' if voto > 0 else '👎'} Guardado: {cual}")
        self._registrar_log(f"DJ: voto {voto:+d} para {cual}")

    def detener(self):
        """Para la sesión (al cerrar la app)."""
        self.sesion.detener()

    # ---------- avisos desde los hilos de fondo ----------

    def _evento_de_fondo(self, tipo, texto):
        if tipo in ("error", "aviso"):
            try:
                self.after(0, lambda: self._mostrar_aviso(tipo, texto))
            except RuntimeError:
                pass  # la ventana se está cerrando

    def _mostrar_aviso(self, tipo, texto):
        self._registrar_log(f"DJ {tipo}: {texto}")
        if tipo == "error":
            messagebox.showerror("Modo DJ", texto)
        else:
            self.etiqueta_voto.config(text=texto)

    # ---------- refresco ----------

    def _tick(self):
        if not self.winfo_exists():
            return
        e = self.sesion.estado()
        if e["sonando"]:
            es_mezcla = e["tipo"] == "mezcla"
            self.etiqueta_titulo.config(text=e["titulo"])
            self.etiqueta_detalle.config(text=f"🎛  Mezclando: {e['receta']}" if es_mezcla else "")
            if es_mezcla:
                self.progreso.config(value=1000 * e["pos_s"] / max(e["total_s"], 1e-6))
                self.etiqueta_tiempo.config(text=f"mezcla {_tiempo(e['pos_s'])} / {_tiempo(e['total_s'])}")
            else:
                self.progreso.config(value=1000 * e["pos_s"] / max(e["total_s"], 1e-6))
                falta = e["fin_s"] - e["pos_s"]
                extra = f"   ·   mezcla en {_tiempo(falta)}" if e["mezcla_lista"] else ""
                self.etiqueta_tiempo.config(text=f"{_tiempo(e['pos_s'])} / {_tiempo(e['total_s'])}{extra}")
            self.medidor.actualizar(*self.sesion.nivel)
            if e["indice"] != self._ultimo_indice and not es_mezcla:
                self._ultimo_indice = e["indice"]
                fila = self._filas.get(e["indice"])
                if fila and self.tabla.exists(fila):
                    self.tabla.see(fila)
            self.boton_mezclar.habilitar(bool(e["mezcla_lista"]))
        else:
            if self._ultimo_indice is not None:
                self._ultimo_indice = None
            self.etiqueta_titulo.config(text="—")
            self.etiqueta_detalle.config(text="")
            self.etiqueta_tiempo.config(text="")
            self.progreso.config(value=0)
            self.medidor.actualizar(0.0, 0.0)
            self.boton_mezclar.habilitar(False)
        self.etiqueta_siguiente.config(text=e.get("siguiente") or "—")
        self.etiqueta_mensaje.config(text=e.get("mensaje") or "")
        self.boton_bien.habilitar(bool(e.get("puede_votar")))
        self.boton_mal.habilitar(bool(e.get("puede_votar")))
        self._actualizar_biblioteca()
        self.after(MS_CUADRO, self._tick)
