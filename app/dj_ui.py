"""
dj_ui.py — pestaña "DJ": elegir una carpeta o USB con música y dejar que el DJ mezcle solo.

  - Izquierda: la biblioteca, con el BPM y la tonalidad (Camelot) de cada canción a medida que
    se analizan. Doble click en una canción: empezar por ella.
  - Derecha: qué suena, por dónde va, cuál viene, qué tipo de mezcla hizo, y los botones
    👍 / 👎 para calificar cada transición (con eso el DJ va aprendiendo tu gusto).
"""

import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import dj_aprender
import dj_sesion
import tema
from widgets import BotonAnimado, MedidorVolumen

MS_CUADRO = 100
VARIADO = "Variado (el DJ decide)"
# texto del desplegable -> límite del bloque en dj_sesion (None = sin límite)
DURACIONES = {"Sin límite": None, "15 minutos": ("min", 15), "30 minutos": ("min", 30), "45 minutos": ("min", 45),
              "1 hora": ("min", 60), "2 horas": ("min", 120), "3 canciones": ("canciones", 3),
              "5 canciones": ("canciones", 5), "10 canciones": ("canciones", 10)}


def unidad_extraible():
    """Letra de la primera unidad extraíble conectada (ej. "E:/"), o None. Solo en Windows."""
    try:
        import ctypes
        import string

        mascara = ctypes.windll.kernel32.GetLogicalDrives()
        for i, letra in enumerate(string.ascii_uppercase):
            if mascara >> i & 1 and ctypes.windll.kernel32.GetDriveTypeW(f"{letra}:\\") == 2:  # 2 = extraíble
                return f"{letra}:/"
    except (AttributeError, OSError):
        pass
    return None


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
        cuerpo = ttk.Frame(self)
        cuerpo.pack(fill="both", expand=True, padx=20, pady=16)

        # ================= izquierda: la música =================
        izquierda = ttk.Frame(cuerpo)
        izquierda.pack(side="left", fill="both", expand=True, padx=(0, 14))

        estado = ttk.Frame(izquierda)
        estado.pack(fill="x", pady=(0, 8))
        ttk.Label(estado, text="Modo DJ", style="Titulo.TLabel").pack(side="left")
        self.etiqueta_preparacion = ttk.Label(estado, text="   Conecta un USB con música: se prepara solo.",
                                              foreground=tema.TEXTO_SUAVE)
        self.etiqueta_preparacion.pack(side="left")
        BotonAnimado(estado, "📂  Cambiar música…", self._elegir_carpeta, alto=28, estilo="suave",
                     ayuda="Otra carpeta de tu PC u otro USB").pack(side="right")
        self._unidad_usb = None
        self._fiesta_pendiente = None   # género elegido que espera tener canciones listas para empezar
        self._valores_mostrados = {}    # índice -> valores que muestra la tabla (para no redibujar de más)

        fiesta = ttk.LabelFrame(izquierda, text="¿Qué quieres escuchar?")
        fiesta.pack(fill="x", pady=(0, 8))
        self.fila_generos = tk.Frame(fiesta, bg=tema.FONDO)
        self.fila_generos.pack(fill="x", padx=8, pady=8)

        marco_tabla = ttk.Frame(izquierda)
        marco_tabla.pack(fill="both", expand=True)
        self.tabla = ttk.Treeview(marco_tabla, columns=("titulo", "artista", "genero", "bpm", "tono"),
                                  show="headings", selectmode="browse")
        for columna, texto, ancho, ancla in (("titulo", "Canción", 250, "w"), ("artista", "Artista", 150, "w"),
                                             ("genero", "Género", 110, "w"), ("bpm", "BPM", 50, "e"),
                                             ("tono", "Tono", 80, "center")):
            self.tabla.heading(columna, text=texto)
            self.tabla.column(columna, width=ancho, anchor=ancla, stretch=columna == "titulo")
        desliz = ttk.Scrollbar(marco_tabla, orient="vertical", command=self.tabla.yview)
        self.tabla.configure(yscrollcommand=desliz.set)
        desliz.pack(side="right", fill="y")
        self.tabla.pack(side="left", fill="both", expand=True)
        self.tabla.tag_configure("sonando", foreground=tema.AZUL)
        self.tabla.tag_configure("error", foreground=tema.ERROR)
        # doble clic: si ya suena algo, «que siga esta»; si no, empezar por ella
        self.tabla.bind("<Double-Button-1>", lambda e: self._doble_clic_tabla())

        # --- opciones avanzadas (plegadas): plan por géneros, mismo artista, estilo de un DJ ---
        self._avanzado_abierto = False
        self.boton_avanzado = ttk.Button(izquierda, text="⚙  Opciones avanzadas  ▸",
                                         command=self._alternar_avanzado)
        self.boton_avanzado.pack(anchor="w", pady=(8, 0))
        self.marco_avanzado = ttk.Frame(izquierda)
        plan = ttk.LabelFrame(self.marco_avanzado, text="Plan de la sesión")
        plan.pack(fill="x", pady=(4, 6))
        fila = ttk.Frame(plan)
        fila.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Label(fila, text="Género:").pack(side="left")
        self.var_genero = tk.StringVar(value=VARIADO)
        self.combo_genero = ttk.Combobox(fila, textvariable=self.var_genero, values=[VARIADO], state="readonly",
                                         width=20)
        self.combo_genero.pack(side="left", padx=(6, 12))
        ttk.Label(fila, text="Durante:").pack(side="left")
        self.var_duracion = tk.StringVar(value="Sin límite")
        ttk.Combobox(fila, textvariable=self.var_duracion, values=list(DURACIONES), state="readonly",
                     width=13).pack(side="left", padx=(6, 12))
        BotonAnimado(fila, "+ Agregar", self._agregar_bloque, alto=28,
                     ayuda="Agrega este género al final del plan. Cuando se cumple su tiempo,\n"
                           "el DJ pasa solo al siguiente.").pack(side="left")
        BotonAnimado(fila, "Quitar último", self._quitar_bloque, alto=28).pack(side="left", padx=6)
        BotonAnimado(fila, "Reiniciar", self._reiniciar_plan, alto=28, estilo="suave").pack(side="left")
        self.etiqueta_plan = ttk.Label(plan, text="", wraplength=640, justify="left", foreground=tema.AZUL)
        self.etiqueta_plan.pack(anchor="w", padx=8)
        self.var_artista = tk.BooleanVar(value=False)
        ttk.Checkbutton(plan, text="Preferir canciones del mismo artista", variable=self.var_artista,
                        command=lambda: setattr(self.sesion, "preferir_artista", self.var_artista.get())).pack(
            anchor="w", padx=8, pady=(2, 8))
        self._plan_inicial = True   # el plan todavía es el de fábrica (solo "Variado")
        self._mostrar_plan()

        salidas = ttk.LabelFrame(self.marco_avanzado, text="Salidas de audio (como en una cabina de DJ)")
        salidas.pack(fill="x", pady=(0, 6))
        fila_s = ttk.Frame(salidas)
        fila_s.pack(fill="x", padx=8, pady=8)
        self._salidas = [(None, "La del sistema")] + dj_sesion.salidas_de_audio()
        nombres = [n for _, n in self._salidas]
        ttk.Label(fila_s, text="Público:").pack(side="left")
        self.var_publico = tk.StringVar(value=nombres[0])
        combo_p = ttk.Combobox(fila_s, textvariable=self.var_publico, values=nombres, state="readonly", width=28)
        combo_p.pack(side="left", padx=(6, 14))
        ttk.Label(fila_s, text="Auriculares (pre-escucha):").pack(side="left")
        self.var_auriculares = tk.StringVar(value="Sin pre-escucha")
        combo_a = ttk.Combobox(fila_s, textvariable=self.var_auriculares, state="readonly", width=28,
                               values=["Sin pre-escucha"] + nombres[1:])
        combo_a.pack(side="left", padx=6)
        for combo in (combo_p, combo_a):
            combo.bind("<<ComboboxSelected>>", lambda e: self._elegir_salidas())

        estilo = ttk.LabelFrame(self.marco_avanzado, text="Estilo aprendido de un DJ")
        estilo.pack(fill="x")
        fila_estilo = ttk.Frame(estilo)
        fila_estilo.pack(fill="x", padx=8, pady=8)
        self.boton_aprender = BotonAnimado(fila_estilo, "🎓  Aprender de una mezcla…", self._aprender, alto=28,
                                           ayuda="Elige el audio (MP3, WAV...) o el video de un DJ mezclando.\n"
                                                 "Se analiza cada cuánto cambia de canción, cuánto duran sus\n"
                                                 "transiciones y qué técnica usa.")
        self.boton_aprender.pack(side="left")
        self.var_estilo = tk.BooleanVar(value=False)
        self.check_estilo = ttk.Checkbutton(fila_estilo, text="Mezclar con este estilo", variable=self.var_estilo,
                                            command=self._aplicar_estilo)
        self.check_estilo.pack(side="left", padx=12)
        self.etiqueta_estilo = ttk.Label(estilo, text="", foreground=tema.TEXTO_SUAVE, wraplength=640,
                                         justify="left")
        self.etiqueta_estilo.pack(anchor="w", padx=8, pady=(0, 8))
        self._perfil = None
        guardados = dj_aprender.cargar_todos()
        self._mostrar_perfil(guardados[0] if guardados else None)

        # ================= derecha: la cabina con el copiloto =================
        derecha = ttk.LabelFrame(cuerpo, text="Cabina")
        derecha.pack(side="left", fill="y")
        caja = tk.Frame(derecha, bg=tema.FONDO, width=460)
        caja.pack(fill="both", expand=True, padx=14, pady=12)
        caja.pack_propagate(False)

        def titulo(texto, arriba=16):
            tk.Label(caja, text=texto, bg=tema.FONDO, fg=tema.TEXTO_SUAVE,
                     font=("TkDefaultFont", 8, "bold")).pack(anchor="w", pady=(arriba, 2))

        titulo("AHORA SUENA", 0)
        self.etiqueta_titulo = tk.Label(caja, text="—", bg=tema.FONDO, fg=tema.TEXTO, anchor="w", justify="left",
                                        wraplength=430, font=("TkDefaultFont", 13, "bold"))
        self.etiqueta_titulo.pack(fill="x")
        self.etiqueta_detalle = tk.Label(caja, text="", bg=tema.FONDO, fg=tema.AZUL, anchor="w")
        self.etiqueta_detalle.pack(fill="x")
        self.progreso = ttk.Progressbar(caja, mode="determinate", maximum=1000)
        self.progreso.pack(fill="x", pady=(8, 2))
        fila_tiempo = tk.Frame(caja, bg=tema.FONDO)
        fila_tiempo.pack(fill="x")
        self.etiqueta_tiempo = tk.Label(fila_tiempo, text="", bg=tema.FONDO, fg=tema.TEXTO_SUAVE, font=("Consolas", 10))
        self.etiqueta_tiempo.pack(side="left")
        self.medidor = MedidorVolumen(fila_tiempo, ancho=150, alto=18)
        self.medidor.pack(side="right")

        titulo("SIGUIENTE")
        self.etiqueta_siguiente = tk.Label(caja, text="—", bg=tema.FONDO, fg=tema.TEXTO, anchor="w", justify="left",
                                           wraplength=430, font=("TkDefaultFont", 10, "bold"))
        self.etiqueta_siguiente.pack(fill="x")
        self.etiqueta_mensaje = tk.Label(caja, text="", bg=tema.FONDO, fg=tema.TEXTO_SUAVE, anchor="w",
                                         justify="left", wraplength=430)
        self.etiqueta_mensaje.pack(fill="x")

        titulo("RECOMENDADAS PARA SEGUIR  ·  un clic la elige")
        self.marco_recos = tk.Frame(caja, bg=tema.FONDO)
        self.marco_recos.pack(fill="x")
        titulo("¿DÓNDE CAMBIAR?  ·  desde donde va la canción")
        self.marco_puntos = tk.Frame(caja, bg=tema.FONDO)
        self.marco_puntos.pack(fill="x")
        self._recos_mostradas = None
        self._puntos_mostrados = None
        self._elegida = None

        controles = tk.Frame(caja, bg=tema.FONDO)
        controles.pack(fill="x", pady=(14, 0))
        self.boton_mezclar = BotonAnimado(controles, "⏭  Mezclar ya", self._mezclar_ya, alto=32,
                                          ayuda="Salta a 5 segundos antes de la mezcla (cuando ya está lista)")
        self.boton_mezclar.pack(side="left")
        BotonAnimado(controles, "■  Detener", self._detener, alto=32).pack(side="left", padx=8)
        self.boton_preescucha = BotonAnimado(controles, "🎧  Escuchar la mezcla", self._preescuchar, alto=32,
                                             ayuda="Suena SOLO por los auriculares: el público no la oye.\n"
                                                   "Elige la salida en Opciones avanzadas.")
        self.boton_preescucha.pack(side="left")

        titulo("¿QUÉ TAL LA ÚLTIMA MEZCLA?")
        votos = tk.Frame(caja, bg=tema.FONDO)
        votos.pack(fill="x")
        self.boton_bien = BotonAnimado(votos, "👍  Me gustó", lambda: self._votar(1), alto=30,
                                       ayuda="El DJ aprende tu gusto (cada 10 votos se reentrena)")
        self.boton_bien.pack(side="left")
        self.boton_mal = BotonAnimado(votos, "👎  No me gustó", lambda: self._votar(-1), alto=30, estilo="peligro",
                                      ayuda="El DJ aprende tu gusto (cada 10 votos se reentrena)")
        self.boton_mal.pack(side="left", padx=8)
        self.etiqueta_voto = tk.Label(caja, text="", bg=tema.FONDO, fg=tema.TEXTO_SUAVE, anchor="w", justify="left",
                                      wraplength=430)
        self.etiqueta_voto.pack(fill="x", pady=(4, 0))

    def _elegir_salidas(self):
        por_nombre = {n: i for i, n in self._salidas}
        publico = por_nombre.get(self.var_publico.get())
        auriculares = por_nombre.get(self.var_auriculares.get())
        if auriculares is not None and auriculares == publico:
            messagebox.showinfo("Modo DJ", "Los auriculares tienen que ser otra salida que la del público.")
            self.var_auriculares.set("Sin pre-escucha")
            auriculares = None
        cambio_publico = publico != self.sesion.salida_publico
        self.sesion.salida_publico, self.sesion.salida_auriculares = publico, auriculares
        if cambio_publico and self.sesion.activa:
            self.etiqueta_voto.config(text="La salida del público cambia la próxima vez que empieces.")

    def _preescuchar(self):
        if self.sesion.salida_auriculares is None:
            self.etiqueta_voto.config(text="Elige la salida de los auriculares en Opciones avanzadas.")
        elif not self.sesion.preescuchar():
            self.etiqueta_voto.config(text="La mezcla todavía no está lista.")
        else:
            self.etiqueta_voto.config(text="🎧 Sonando en los auriculares: así va a quedar el cambio.")

    def _alternar_avanzado(self):
        self._avanzado_abierto = not self._avanzado_abierto
        if self._avanzado_abierto:
            self.marco_avanzado.pack(fill="x", after=self.boton_avanzado)
            self.boton_avanzado.config(text="⚙  Opciones avanzadas  ▾")
        else:
            self.marco_avanzado.pack_forget()
            self.boton_avanzado.config(text="⚙  Opciones avanzadas  ▸")

    def _doble_clic_tabla(self):
        elegida = self.tabla.selection()
        indice = next((i for i, fila in self._filas.items() if elegida and fila == elegida[0]), None)
        if indice is None:
            return
        if self.sesion.activa:
            self._elegir_siguiente(indice)
        else:
            self._empezar(desde_seleccion=True)

    # ---------- copiloto ----------

    def _elegir_siguiente(self, indice):
        if indice == self._ultimo_indice:
            return
        self._elegida = indice
        self.sesion.pedir(indice=indice)
        c = self.sesion.biblioteca[indice]
        self.etiqueta_voto.config(text=f"Preparando la mezcla con «{c['titulo']}»…")
        self._recos_mostradas = None  # se redibuja con la elegida marcada

    def _cambiar_en(self, segundo):
        self.sesion.pedir(indice=self._elegida, entrar_s=segundo)
        self.etiqueta_voto.config(text=f"El cambio se hará en {_tiempo(segundo)} (inicio de frase más cercano).")

    def _limpiar_copiloto(self):
        for marco in (self.marco_recos, self.marco_puntos):
            for hijo in marco.winfo_children():
                hijo.destroy()
        self._recos_mostradas = self._puntos_mostrados = None
        self._elegida = None

    def _actualizar_copiloto(self):
        if not self.sesion.activa:
            if self._recos_mostradas is not None:
                self._limpiar_copiloto()
            return
        recos = self.sesion.recomendar()
        clave = [(r["indice"], tuple(r["razones"])) for r in recos] + [self._elegida]
        if clave != self._recos_mostradas:
            self._recos_mostradas = clave
            for hijo in self.marco_recos.winfo_children():
                hijo.destroy()
            for r in recos:
                c = self.sesion.biblioteca[r["indice"]]
                elegida = r["indice"] == self._elegida
                fondo = tema.AZUL if elegida else tema.FONDO
                fila = tk.Frame(self.marco_recos, bg=fondo, cursor="hand2")
                fila.pack(fill="x", pady=1)
                nombre = tk.Label(fila, text=f"{c['artista'] or ''} – {c['titulo']}", anchor="w", bg=fondo,
                                  fg=tema.FONDO if elegida else tema.TEXTO, font=("TkDefaultFont", 9, "bold"))
                nombre.pack(fill="x", padx=6, pady=(3, 0))
                razones = tk.Label(fila, text=" · ".join(r["razones"]), anchor="w", justify="left", wraplength=420,
                                   bg=fondo, fg=tema.FONDO if elegida else tema.TEXTO_SUAVE,
                                   font=("TkDefaultFont", 8))
                razones.pack(fill="x", padx=6, pady=(0, 3))
                for w in (fila, nombre, razones):
                    w.bind("<Button-1>", lambda e, i=r["indice"]: self._elegir_siguiente(i))
        puntos = self.sesion.puntos_de_cambio()
        clave_p = [(int(p["segundo"]), p["motivo"]) for p in puntos]
        if clave_p != self._puntos_mostrados:
            self._puntos_mostrados = clave_p
            for hijo in self.marco_puntos.winfo_children():
                hijo.destroy()
            if not puntos:
                tk.Label(self.marco_puntos, text="Ya no hay tiempo de preparar otro punto: cambia al final.",
                         bg=tema.FONDO, fg=tema.TEXTO_SUAVE, anchor="w").pack(fill="x")
            for p in puntos:
                BotonAnimado(self.marco_puntos, f"{_tiempo(p['segundo'])}  ·  {p['motivo']}",
                             lambda s=p["segundo"]: self._cambiar_en(s), alto=26, estilo="suave",
                             ayuda="La mezcla empieza en el inicio de frase más cercano a este punto").pack(
                    fill="x", pady=1)

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
        generos = self.sesion.generos()
        self._armar_botones_genero(generos)
        self._valores_mostrados = {}
        self.combo_genero.config(values=[VARIADO] + [f"{g}  ({n})" for g, n in generos.items()])
        self.var_genero.set(VARIADO)
        self._reiniciar_plan()
        self._registrar_log(f"DJ: carpeta {carpeta} ({cantidad} canciones, géneros: {', '.join(generos) or '—'})")
        self._actualizar_biblioteca()
        if cantidad == 0:
            messagebox.showinfo("Modo DJ", "No se encontraron archivos de audio en esa carpeta.")

    @staticmethod
    def _valores(cancion):
        base = (cancion["titulo"], cancion["artista"] or "", cancion["genero"])
        if cancion["error"]:
            return base + ("—", "no sirve")
        if cancion["bpm"] is None:
            return base + ("…", "…")
        return base + (f"{cancion['bpm']:.0f}", f"{cancion['camelot']} · {cancion['tono']}")

    # ---------- plan de la sesión ----------

    def _mostrar_plan(self):
        self.etiqueta_plan.config(text=self.sesion.describir_plan())

    def _agregar_bloque(self):
        genero = self.var_genero.get().split("  (")[0]
        bloque = {"genero": None if genero == VARIADO else genero, "limite": DURACIONES[self.var_duracion.get()]}
        if self._plan_inicial:      # el primer género que agregas reemplaza al "Variado" de fábrica
            self.sesion.plan = [bloque]
            self._plan_inicial = False
        else:
            self.sesion.plan.append(bloque)
        self._mostrar_plan()

    def _quitar_bloque(self):
        if len(self.sesion.plan) > 1:
            self.sesion.plan.pop()
        else:
            self._reiniciar_plan()
        self._mostrar_plan()

    def _reiniciar_plan(self):
        self.sesion.plan = [{"genero": None, "limite": None}]
        self._plan_inicial = True
        self._mostrar_plan()

    # ---------- lo simple: un botón por género ----------

    MINIMO_PARA_EMPEZAR = 6  # canciones preparadas del género para arrancar sin esperar al resto

    def _armar_botones_genero(self, generos):
        for hijo in self.fila_generos.winfo_children():
            hijo.destroy()
        BotonAnimado(self.fila_generos, "🎉  Todo variado", lambda: self._fiesta(None), estilo="acento", alto=40,
                     ayuda="El DJ mezcla de todo y cambia de género cada tanto").pack(side="left", padx=(0, 8))
        for genero, cantidad in generos.items():
            BotonAnimado(self.fila_generos, f"🎵  {genero}", lambda g=genero: self._fiesta(g), alto=40,
                         ayuda=f"{cantidad} canciones de {genero}").pack(side="left", padx=(0, 8))

    def _fiesta(self, genero):
        """Un clic y suena: el DJ mezcla ese género (o variado) sin límite de tiempo."""
        if not self.sesion.biblioteca:
            messagebox.showinfo("Modo DJ", "Conecta un USB con música o elige una carpeta.")
            return
        self.sesion.detener()
        self.sesion.genero_prioridad = genero          # se prepara primero lo que se va a escuchar
        self.sesion.plan = [{"genero": genero, "limite": None}]
        self._plan_inicial = genero is None
        self._mostrar_plan()
        self.tabla.selection_remove(*self.tabla.selection())
        self._fiesta_pendiente = genero if genero is not None else VARIADO
        self._intentar_fiesta()

    def _intentar_fiesta(self):
        if self._fiesta_pendiente is None:
            return
        genero = None if self._fiesta_pendiente == VARIADO else self._fiesta_pendiente
        if self.sesion.listas_del_genero(genero) >= min(self.MINIMO_PARA_EMPEZAR,
                                                        self.sesion.generos().get(genero, 10 ** 6)):
            self._fiesta_pendiente = None
            self._empezar()

    def _mostrar_preparacion(self):
        total = len(self.sesion.biblioteca)
        if not total:
            return
        hechas = self.sesion.preparadas()
        equipo = self.sesion.equipo
        modo = "mezclas con IA" if equipo.get("separar_con_ia", True) else "mezclas por ecualizador (equipo modesto)"
        if hechas >= total:
            texto = f"   ✔ Tu música está lista: {self.sesion.analizadas()} canciones · {modo}"
            prof = self.sesion.profunda
            if prof and prof["hechas"] < prof["total"]:
                texto += (f" · instrumentos {100 * prof['hechas'] // prof['total']} %"
                          + (" (en pausa mientras suena)" if self.sesion.activa else ""))
        else:
            falta = self.sesion.segundos_restantes()
            estimado = f" · quedan ~{max(1, round(falta / 60))} min" if falta else ""
            texto = (f"   Preparando tu música… {100 * hechas // total} %{estimado} · "
                     f"{equipo.get('procesos', 1)} procesos en paralelo")
        if self._fiesta_pendiente is not None:
            texto = f"Preparando {self._fiesta_pendiente}: empieza en cuanto haya canciones listas…   " + texto
        if self.etiqueta_preparacion.cget("text") != texto:
            self.etiqueta_preparacion.config(text=texto)

    # ---------- estilo aprendido ----------

    def _mostrar_perfil(self, perfil):
        self._perfil = perfil if perfil and perfil.get("resumen") else None
        if self._perfil is None:
            self.etiqueta_estilo.config(text="Todavía no se aprendió ningún estilo." if perfil is None
                                        else "En esa mezcla no se detectaron cambios de canción.")
            self.check_estilo.state(["disabled"])
            self.var_estilo.set(False)
        else:
            self.etiqueta_estilo.config(text=f"«{perfil['nombre']}»\n{dj_aprender.describir(perfil)}")
            self.check_estilo.state(["!disabled"])
        self._aplicar_estilo()

    def _aplicar_estilo(self):
        self.sesion.estilo = self._perfil["resumen"] if self._perfil and self.var_estilo.get() else None

    def _aprender(self):
        ruta = filedialog.askopenfilename(title="Mezcla de un DJ (audio o video)", filetypes=[
            ("Audio y video", "*.mp3 *.wav *.m4a *.flac *.ogg *.aac *.mp4 *.mkv *.mov *.webm"),
            ("Todos los archivos", "*.*")])
        if ruta:
            self.aprender_de(ruta)

    def aprender_de(self, ruta):
        self.boton_aprender.habilitar(False)
        self.etiqueta_estilo.config(text="Analizando la mezcla...")

        def avance(fraccion, texto):
            try:
                self.after(0, lambda: self.etiqueta_estilo.config(text=f"{texto} {fraccion * 100:.0f}%"))
            except RuntimeError:
                pass

        def trabajo():
            try:
                perfil = dj_aprender.analizar_mezcla(ruta, al_avanzar=avance)
                dj_aprender.guardar(perfil)
                fin = lambda: self._aprendido(perfil, None)
            except Exception as error:
                mensaje = str(error)
                fin = lambda: self._aprendido(None, mensaje)
            try:
                self.after(0, fin)
            except RuntimeError:
                pass

        threading.Thread(target=trabajo, daemon=True).start()

    def _aprendido(self, perfil, error):
        self.boton_aprender.habilitar(True)
        if error:
            self.etiqueta_estilo.config(text="")
            messagebox.showerror("Aprender de una mezcla", error)
            return
        self.var_estilo.set(bool(perfil.get("resumen")))
        self._mostrar_perfil(perfil)
        self._registrar_log(f"DJ: estilo aprendido de «{perfil['nombre']}»: {perfil.get('resumen')}")

    # ---------- USB ----------

    def _buscar_usb(self):
        """Al conectar un USB, si no hay música cargada, se carga y se prepara solo."""
        unidad = unidad_extraible()
        if unidad != self._unidad_usb:
            self._unidad_usb = unidad
            if unidad and not self.sesion.biblioteca and not self.sesion.activa:
                self.abrir_carpeta(unidad)  # el usuario no tiene que hacer nada

    def _actualizar_biblioteca(self):
        biblioteca = self.sesion.biblioteca
        for i, fila in self._filas.items():
            if i >= len(biblioteca):
                continue
            cancion = biblioteca[i]
            etiquetas = ("error",) if cancion["error"] else (("sonando",) if i == self._ultimo_indice else ())
            clave = (tuple(self._valores(cancion)), etiquetas)
            if self._valores_mostrados.get(i) != clave and self.tabla.exists(fila):
                self.tabla.item(fila, values=clave[0], tags=etiquetas)
                self._valores_mostrados[i] = clave

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
        if tipo == "recomendaciones":  # empezó otra canción: el copiloto recomienda de nuevo
            try:
                self.after(0, self._limpiar_copiloto)
                self.after(50, self._actualizar_copiloto)
            except RuntimeError:
                pass
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
            quien = "  ·  ".join(x for x in (e.get("artista"), e.get("genero")) if x)
            if len(self.sesion.plan) > 1:
                quien += f"  ·  parte {e['bloque'] + 1} de {len(self.sesion.plan)} del plan"
            self.etiqueta_detalle.config(text=f"🎛  Mezclando: {e['receta']}" if es_mezcla else quien)
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
            self.boton_preescucha.habilitar(bool(e["mezcla_lista"]) and self.sesion.salida_auriculares is not None)
        else:
            self.boton_preescucha.habilitar(False)
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
        self._cuadros = getattr(self, "_cuadros", 0) + 1
        if self._cuadros % 10 == 1:  # una vez por segundo: tabla, preparación y ¿ya puede empezar?
            self._actualizar_biblioteca()
            self._mostrar_preparacion()
            self._intentar_fiesta()
        if self._cuadros % 20 == 5:  # cada 2 s: recomendaciones y puntos de cambio al día
            self._actualizar_copiloto()
        if self._cuadros % 30 == 1:  # cada 3 s: ¿se conectó o se sacó un USB?
            self._buscar_usb()
        self.after(MS_CUADRO, self._tick)
