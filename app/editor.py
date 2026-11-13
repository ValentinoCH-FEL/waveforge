"""
editor.py — pestaña "Moldear": editor de pistas en línea de tiempo.

  - Cada pista en su carril: volumen, M (silenciar), S (solo), tamaño y eliminar.
  - Zoom (Ctrl+rueda, botones +/−) y desplazamiento (Shift+rueda, barra inferior).
  - Seleccionar un tramo arrastrando sobre una o varias pistas, y aplicarle solo ahí:
    subir/bajar volumen, silenciar, fade in/out o un efecto (ecualizador, filtros...).
  - Deshacer / rehacer todo (Ctrl+Z / Ctrl+Y). El audio original nunca se modifica.
  - Cabezal de reproducción animado, vúmetro, reloj y exportar.
"""

import os
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np

import audio_io
import ediciones
import filtros_registry
import procesamiento
import reproductor
import tema
from widgets import Ayuda, BotonAnimado, MedidorVolumen, mezclar_color

ANCHO_CABECERA = 250
ALTOS = (60, 100, 170)      # alto de un carril: chico / normal / grande
ALTO_REGLA = 26
MS_CUADRO = 33              # ~30 cuadros por segundo para el cabezal y el vúmetro
PASOS_ZOOM = 6              # cuadros que dura la animación de zoom
MIN_MUESTRAS_VISTA = 2048   # zoom máximo (~0.05 s en pantalla)


def formato_tiempo(segundos, decimas=False):
    segundos = max(0.0, segundos)
    m, s = divmod(segundos, 60)
    return f"{int(m)}:{s:04.1f}" if decimas else f"{int(m)}:{int(s):02d}"


class EditorMoldear(ttk.Frame):
    def __init__(self, padre, motor, mezclador, registrar_log, al_pedir_cancion):
        super().__init__(padre)
        self.motor = motor
        self.mezclador = mezclador
        self._registrar_log = registrar_log
        self._al_pedir_cancion = al_pedir_cancion

        self.info = None
        self.nombre = "audio"
        self.pistas = []          # pistas originales: {id, etiqueta, color, izq, der}
        self.estado = {}          # id -> estado de edición de esa pista (ver _crear_estado)
        self.historial = ediciones.Historial()
        self.seleccion = None     # (i0, i1, [ids de pista]) o None
        self._v0, self._v1 = 0, 1  # muestras visibles [v0, v1)
        self._arrastre = None
        self._anim_zoom = None
        self._suena_original = False
        self._contenido = None
        self._tick_activo = False

        self.mostrar_vacio("Todavía no hay ninguna canción.\nCárgala y sepárala en la pestaña «Separación con IA».",
                           con_boton=True)

    # =====================================================================
    # Estados de la pestaña
    # =====================================================================

    def _limpiar(self):
        self.mezclador.detener()
        if self._contenido is not None:
            self._contenido.destroy()
        self._contenido = ttk.Frame(self)
        self._contenido.pack(fill="both", expand=True)
        self.lienzo = None

    def mostrar_vacio(self, texto, con_boton=False):
        """Pantalla de espera (sin canción, o mientras la IA separa)."""
        self._limpiar()
        self.info = None
        caja = ttk.Frame(self._contenido)
        caja.place(relx=0.5, rely=0.45, anchor="center")
        ttk.Label(caja, text="〰", font=("TkDefaultFont", 34), foreground=tema.AZUL).pack()
        ttk.Label(caja, text=texto, foreground=tema.TEXTO_SUAVE, justify="center",
                  font=("TkDefaultFont", 11)).pack(pady=(4, 14))
        if con_boton:
            BotonAnimado(caja, "Cargar una canción", self._al_pedir_cancion, estilo="acento", alto=36,
                         fuente=("TkDefaultFont", 10, "bold")).pack()

    def cargar(self, info, nombre, pistas, nota=""):
        """Abre el editor con estas pistas (ya separadas, o una sola con la canción entera)."""
        self._limpiar()
        self.info, self.nombre, self.pistas = info, nombre, pistas
        self.estado = {p["id"]: self._crear_estado(p) for p in pistas}
        self.historial.limpiar()
        self.seleccion = None
        self._suena_original = False
        self._v0, self._v1 = 0, self._largo()
        self._construir(nota)
        self._configurar_mezclador()
        if not self._tick_activo:
            self._tick_activo = True
            self._tick()

    def _crear_estado(self, pista):
        return {
            "ops": [],
            "render": (pista["izq"], pista["der"]),
            "picos": ediciones.Picos(pista["izq"]),
            "pico_max": max(float(np.abs(pista["izq"]).max()) if len(pista["izq"]) else 0.0, 1e-4),
            "alto": 1,
            "eliminada": False,
            "vol": tk.DoubleVar(value=100.0),
            "mute": tk.BooleanVar(value=False),
            "solo": tk.BooleanVar(value=False),
        }

    # =====================================================================
    # Construcción de la interfaz
    # =====================================================================

    def _construir(self, nota):
        raiz = self._contenido

        # --- cabecera: nombre y exportar ---
        cab = ttk.Frame(raiz)
        cab.pack(fill="x", padx=16, pady=(10, 4))
        ttk.Label(cab, text=self.nombre, style="Titulo.TLabel").pack(side="left")
        ttk.Label(cab, text=f"  ·  {self.info.resumen()}" + (f"  ·  {nota}" if nota else ""),
                  foreground=tema.TEXTO_SUAVE).pack(side="left")
        self.boton_exportar = BotonAnimado(cab, "⤓  Exportar mezcla", self._exportar_mezcla, estilo="acento",
                                           ayuda="Guarda la mezcla completa como WAV", alto=32)
        self.boton_exportar.pack(side="right")
        self.boton_exportar_pistas = BotonAnimado(cab, "⤓  Exportar pistas", self._exportar_pistas, alto=32,
                                                  ayuda="Guarda cada pista (con sus ediciones) en un WAV aparte")
        self.boton_exportar_pistas.pack(side="right", padx=(0, 8))
        if self.motor is None:
            self.boton_exportar.habilitar(False)
            self.boton_exportar_pistas.habilitar(False)

        # --- transporte ---
        barra = tk.Frame(raiz, bg=tema.PANEL)
        barra.pack(fill="x", padx=16, pady=(4, 0))
        interior = tk.Frame(barra, bg=tema.PANEL)
        interior.pack(fill="x", padx=10, pady=8)
        BotonAnimado(interior, "⏮", self._al_inicio, ancho=40, ayuda="Al inicio (Inicio)").pack(side="left")
        self.boton_play = BotonAnimado(interior, "▶", self._alternar_reproduccion, estilo="acento", ancho=52,
                                       ayuda="Reproducir / pausa (Espacio)")
        self.boton_play.pack(side="left", padx=4)
        BotonAnimado(interior, "■", self._detener, ancho=40, ayuda="Detener y volver al inicio").pack(side="left")
        self.var_original = tk.BooleanVar(value=False)
        BotonAnimado(interior, "A/B original", self._cambiar_ab, alternable=True, variable=self.var_original,
                     color_encendido="#f2a33a",
                     ayuda="Escuchar la canción original, sin separar ni editar, en el mismo segundo").pack(
            side="left", padx=(10, 0))
        self.reloj = tk.Label(interior, text="0:00.0", bg=tema.PANEL, fg=tema.TEXTO,
                              font=("Consolas", 18, "bold"))
        self.reloj.pack(side="left", padx=(18, 4))
        self.reloj_total = tk.Label(interior, text=f"/ {formato_tiempo(self._duracion())}", bg=tema.PANEL,
                                    fg=tema.TEXTO_SUAVE, font=("Consolas", 11))
        self.reloj_total.pack(side="left", pady=(6, 0))
        self.medidor = MedidorVolumen(interior)
        self.medidor.pack(side="left", padx=(18, 0))

        self.boton_rehacer = BotonAnimado(interior, "↷", self.rehacer, ancho=40, ayuda="Rehacer (Ctrl+Y)")
        self.boton_rehacer.pack(side="right")
        self.boton_deshacer = BotonAnimado(interior, "↶", self.deshacer, ancho=40, ayuda="Deshacer (Ctrl+Z)")
        self.boton_deshacer.pack(side="right", padx=(12, 4))
        BotonAnimado(interior, "⤢", self.zoom_ajustar, ancho=40, ayuda="Ver la canción completa").pack(side="right")
        BotonAnimado(interior, "+", lambda: self.zoom(2.0), ancho=36, ayuda="Acercar (Ctrl+rueda, +)").pack(
            side="right", padx=4)
        BotonAnimado(interior, "−", lambda: self.zoom(0.5), ancho=36, ayuda="Alejar (Ctrl+rueda, −)").pack(
            side="right")
        tk.Label(interior, text="Zoom", bg=tema.PANEL, fg=tema.TEXTO_SUAVE).pack(side="right", padx=(0, 6))

        # --- herramientas para el tramo seleccionado ---
        herr = tk.Frame(raiz, bg=tema.PANEL_2)
        herr.pack(fill="x", padx=16)
        fila = tk.Frame(herr, bg=tema.PANEL_2)
        fila.pack(fill="x", padx=10, pady=6)
        self.etiqueta_sel = tk.Label(fila, text="", bg=tema.PANEL_2, fg=tema.TEXTO_SUAVE, width=34, anchor="w")
        self.etiqueta_sel.pack(side="left")
        self._botones_tramo = []
        for texto, ayuda, accion in (
            ("🔉 −3 dB", "Bajar el volumen del tramo", lambda: self._aplicar({"tipo": "ganancia", "db": -3.0})),
            ("🔊 +3 dB", "Subir el volumen del tramo", lambda: self._aplicar({"tipo": "ganancia", "db": 3.0})),
            ("🔇 Silenciar", "Dejar el tramo en silencio (Supr)", lambda: self._aplicar({"tipo": "silencio"})),
            ("◢ Fade in", "Que el tramo entre de a poco", lambda: self._aplicar({"tipo": "fade_in"})),
            ("◣ Fade out", "Que el tramo se apague de a poco", lambda: self._aplicar({"tipo": "fade_out"})),
            ("⟲ Limpiar", "Quitar las ediciones que tocan el tramo", self._limpiar_tramo),
        ):
            b = BotonAnimado(fila, texto, accion, ayuda=ayuda, alto=28,
                             estilo="peligro" if "Limpiar" in texto else "normal")
            b.pack(side="left", padx=(0, 6))
            self._botones_tramo.append(b)

        efectos = [(i, e) for i, e in filtros_registry.listar() if i != "ninguno"]
        self._efectos = dict((e, i) for i, e in efectos)
        self.var_efecto = tk.StringVar(value=efectos[0][1])
        combo = ttk.Combobox(fila, textvariable=self.var_efecto, values=[e for _, e in efectos], state="readonly",
                             width=22)
        combo.pack(side="left", padx=(14, 6))
        combo.bind("<<ComboboxSelected>>", lambda e: self._preparar_parametro())
        self.var_param = tk.DoubleVar()
        self.escala_param = ttk.Scale(fila, variable=self.var_param, orient="horizontal", length=140,
                                      command=lambda v: self._mostrar_param())
        self.escala_param.pack(side="left")
        self.etiqueta_param = tk.Label(fila, text="", bg=tema.PANEL_2, fg=tema.TEXTO, width=12, anchor="w")
        self.etiqueta_param.pack(side="left", padx=(6, 6))
        b = BotonAnimado(fila, "✦ Aplicar efecto", self._aplicar_efecto, estilo="acento", alto=28,
                         ayuda="Aplica el efecto elegido solo al tramo seleccionado")
        b.pack(side="left")
        self._botones_tramo.append(b)
        self._preparar_parametro()

        # --- zona de pistas: regla + lienzo con carriles ---
        zona = ttk.Frame(raiz)
        zona.pack(fill="both", expand=True, padx=16, pady=(8, 0))
        self.regla = tk.Canvas(zona, height=ALTO_REGLA, bg=tema.PANEL, highlightthickness=0, bd=0)
        self.regla.pack(fill="x")
        cuerpo = ttk.Frame(zona)
        cuerpo.pack(fill="both", expand=True)
        self.lienzo = tk.Canvas(cuerpo, bg=tema.FONDO, highlightthickness=0, bd=0)
        barra_v = ttk.Scrollbar(cuerpo, orient="vertical", command=self.lienzo.yview)
        self.lienzo.configure(yscrollcommand=barra_v.set)
        barra_v.pack(side="right", fill="y")
        self.lienzo.pack(side="left", fill="both", expand=True)
        self.barra_h = ttk.Scrollbar(zona, orient="horizontal", command=self._scroll_x)
        self.barra_h.pack(fill="x")

        ayuda = tk.Label(raiz, bg=tema.FONDO, fg=tema.TEXTO_SUAVE, anchor="w", font=("TkDefaultFont", 8),
                         text="Arrastra sobre una pista para elegir un tramo (hacia arriba/abajo abarca más pistas) · "
                              "Doble click: pista entera · Ctrl+A: todo · Click: mover el cabezal · "
                              "Ctrl+rueda: zoom · Shift+rueda: moverse · Espacio: reproducir · Supr: silenciar")
        ayuda.pack(fill="x", padx=16, pady=(4, 8))

        self.lienzo.bind("<Configure>", lambda e: self._redibujar())
        self.lienzo.bind("<ButtonPress-1>", self._presionar)
        self.lienzo.bind("<B1-Motion>", self._arrastrar)
        self.lienzo.bind("<ButtonRelease-1>", self._soltar)
        self.lienzo.bind("<Double-Button-1>", self._doble_click)
        self.lienzo.bind("<MouseWheel>", self._rueda)
        self.regla.bind("<ButtonPress-1>", self._click_regla)
        self.regla.bind("<B1-Motion>", self._click_regla)
        self.regla.bind("<MouseWheel>", self._rueda)

        self._construir_carriles()
        self._actualizar_botones()

    def _construir_carriles(self):
        """Cabecera de cada pista (controles), incrustada en el lienzo a la altura de su carril."""
        self.lienzo.delete("cabecera")
        self._cabeceras = {}
        y = 0
        for pista in self._visibles():
            est = self.estado[pista["id"]]
            alto = ALTOS[est["alto"]]
            marco = tk.Frame(self.lienzo, bg=tema.PANEL, width=ANCHO_CABECERA, height=alto - 2)
            marco.pack_propagate(False)
            tk.Frame(marco, bg=pista["color"], width=5).pack(side="left", fill="y")
            dentro = tk.Frame(marco, bg=tema.PANEL)
            dentro.pack(side="left", fill="both", expand=True, padx=(8, 6), pady=4)

            arriba = tk.Frame(dentro, bg=tema.PANEL)
            arriba.pack(fill="x")
            tk.Label(arriba, text=pista["etiqueta"], bg=tema.PANEL, fg=tema.TEXTO,
                     font=("TkDefaultFont", 9, "bold"), anchor="w").pack(side="left")
            BotonAnimado(arriba, "✕", lambda i=pista["id"]: self._eliminar_pista(i), estilo="peligro", ancho=24,
                         alto=22, ayuda="Quitar esta pista de la mezcla (se puede deshacer)").pack(side="right")
            BotonAnimado(arriba, "⇕", lambda i=pista["id"]: self._cambiar_alto(i), ancho=24, alto=22,
                         ayuda="Agrandar / achicar la pista").pack(side="right", padx=2)
            BotonAnimado(arriba, "S", self._mezcla_cambio, alternable=True, variable=est["solo"],
                         color_encendido="#f2a33a", ancho=24, alto=22, ayuda="Solo: escuchar solo esta pista").pack(
                side="right", padx=2)
            BotonAnimado(arriba, "M", self._mezcla_cambio, alternable=True, variable=est["mute"],
                         color_encendido=tema.ROJO, ancho=24, alto=22, ayuda="Silenciar esta pista").pack(side="right")

            if alto > ALTOS[0]:
                medio = tk.Frame(dentro, bg=tema.PANEL)
                medio.pack(fill="x", pady=(4, 0))
                etiqueta = tk.Label(medio, text=f"{est['vol'].get():.0f}%", bg=tema.PANEL, fg=tema.TEXTO_SUAVE,
                                    width=5, anchor="e")
                escala = ttk.Scale(medio, from_=0, to=150, variable=est["vol"], orient="horizontal",
                                   command=lambda v, e=etiqueta: (e.config(text=f"{float(v):.0f}%"),
                                                                  self._mezcla_cambio()))
                escala.pack(side="left", fill="x", expand=True)
                etiqueta.pack(side="left")
                Ayuda(escala, "Volumen de la pista (0-150 %)")
            if alto >= ALTOS[2] and est["ops"]:
                for op in est["ops"][-4:]:
                    tk.Label(dentro, text=f"• {ediciones.describir(op, self.info.muestreo)}  "
                                          f"({formato_tiempo(op['i0'] / self.info.muestreo)}–"
                                          f"{formato_tiempo(op['i1'] / self.info.muestreo)})",
                             bg=tema.PANEL, fg=tema.TEXTO_SUAVE, font=("TkDefaultFont", 8), anchor="w").pack(fill="x")

            # resaltar la cabecera al pasar el mouse
            for w in (marco, dentro, arriba):
                w.bind("<Enter>", lambda e, m=marco: self._resaltar(m, True), add="+")
                w.bind("<Leave>", lambda e, m=marco: self._resaltar(m, False), add="+")
            self.lienzo.create_window(0, y, window=marco, anchor="nw", tags="cabecera")
            self._cabeceras[pista["id"]] = marco
            y += alto
        self._alto_total = y
        self.lienzo.configure(scrollregion=(0, 0, 1, max(y, 1)))
        self._redibujar()

    def _resaltar(self, marco, encendido):
        color = "#24242b" if encendido else tema.PANEL
        for w in [marco] + [h for h in marco.winfo_children()[1:]]:
            _pintar_fondo(w, color)

    # =====================================================================
    # Datos
    # =====================================================================

    def _largo(self):
        return len(self.pistas[0]["izq"]) if self.pistas else 1

    def _duracion(self):
        return self._largo() / float(self.info.muestreo)

    def _visibles(self):
        return [p for p in self.pistas if not self.estado[p["id"]]["eliminada"]]

    def _ganancias(self):
        hay_solo = any(self.estado[p["id"]]["solo"].get() for p in self._visibles())
        ganancias = []
        for p in self.pistas:
            est = self.estado[p["id"]]
            activa = (not est["eliminada"] and not est["mute"].get()
                      and (not hay_solo or est["solo"].get()))
            ganancias.append(est["vol"].get() / 100.0 if activa else 0.0)
        return ganancias

    def _configurar_mezclador(self):
        if self.info is None:
            return
        if self._suena_original:
            p = self._original
            self.mezclador.configurar(self.info.muestreo, [(p[0], p[1])], [1.0])
        else:
            self.mezclador.configurar(self.info.muestreo, [self.estado[p["id"]]["render"] for p in self.pistas],
                                      self._ganancias())

    def fijar_original(self, izquierdo, derecho):
        """La canción sin separar, para el botón A/B."""
        self._original = (izquierdo, derecho)

    def _mezcla_cambio(self):
        if not self._suena_original and self.mezclador.cantidad_pistas == len(self.pistas):
            self.mezclador.cambiar_ganancias(self._ganancias())
        self._redibujar()

    def _rerenderizar(self, ids):
        for id_p in ids:
            pista = next(p for p in self.pistas if p["id"] == id_p)
            est = self.estado[id_p]
            est["render"] = ediciones.renderizar(self.motor, pista["izq"], pista["der"], est["ops"], self.info.muestreo)
            est["picos"] = ediciones.Picos(est["render"][0])
        self._configurar_mezclador()

    # =====================================================================
    # Ediciones, deshacer y rehacer
    # =====================================================================

    def _aplicar(self, plantilla):
        if not self.seleccion:
            return
        i0, i1, ids = self.seleccion
        cambios = []
        for id_p in ids:
            op = dict(plantilla, i0=i0, i1=i1)
            self.estado[id_p]["ops"].append(op)
            cambios.append((id_p, op))
        self.historial.registrar({"tipo": "ops", "cambios": cambios})
        self._despues_de_editar([c[0] for c in cambios],
                                f"{ediciones.describir(cambios[0][1], self.info.muestreo)} en {len(ids)} pista(s)")

    def _aplicar_efecto(self):
        id_filtro = self._efectos[self.var_efecto.get()]
        info = filtros_registry.obtener(id_filtro)
        valor = self.var_param.get() if info["parametro"] else 0.0
        self._aplicar({"tipo": "efecto", "filtro": id_filtro, "valor": round(valor, 2)})

    def _limpiar_tramo(self):
        if not self.seleccion:
            return
        i0, i1, ids = self.seleccion
        quitados = []
        for id_p in ids:
            ops = self.estado[id_p]["ops"]
            for indice in range(len(ops) - 1, -1, -1):
                op = ops[indice]
                if op["i0"] < i1 and op["i1"] > i0:
                    quitados.append((id_p, op, indice))
                    del ops[indice]
        if quitados:
            self.historial.registrar({"tipo": "limpiar", "cambios": quitados})
            self._despues_de_editar({q[0] for q in quitados}, f"Se quitaron {len(quitados)} edición(es)")

    def _eliminar_pista(self, id_p):
        if len(self._visibles()) <= 1:
            messagebox.showinfo("Moldear", "Tiene que quedar al menos una pista.")
            return
        self.estado[id_p]["eliminada"] = True
        self.historial.registrar({"tipo": "eliminar", "id": id_p})
        if self.seleccion:
            i0, i1, ids = self.seleccion
            ids = [i for i in ids if i != id_p]
            self.seleccion = (i0, i1, ids) if ids else None
        self._configurar_mezclador()
        self._construir_carriles()
        self._actualizar_botones()

    def _cambiar_alto(self, id_p):
        est = self.estado[id_p]
        est["alto"] = (est["alto"] + 1) % len(ALTOS)
        self._construir_carriles()

    def deshacer(self):
        accion = self.historial.deshacer()
        if accion:
            self._revertir(accion, deshacer=True)

    def rehacer(self):
        accion = self.historial.rehacer()
        if accion:
            self._revertir(accion, deshacer=False)

    def _revertir(self, accion, deshacer):
        tipo = accion["tipo"]
        if tipo == "eliminar":
            self.estado[accion["id"]]["eliminada"] = not deshacer
            self._configurar_mezclador()
            self._construir_carriles()
            self._actualizar_botones()
            return
        ids = set()
        if tipo == "ops":
            for id_p, op in accion["cambios"]:
                ops = self.estado[id_p]["ops"]
                if deshacer:
                    ops[:] = [o for o in ops if o is not op]
                else:
                    ops.append(op)
                ids.add(id_p)
        elif tipo == "limpiar":
            cambios = accion["cambios"]
            for id_p, op, indice in (reversed(cambios) if deshacer else cambios):
                ops = self.estado[id_p]["ops"]
                if deshacer:
                    ops.insert(indice, op)
                else:
                    ops[:] = [o for o in ops if o is not op]
                ids.add(id_p)
        self._despues_de_editar(ids, "Deshecho" if deshacer else "Rehecho", registrar=False)

    def _despues_de_editar(self, ids, mensaje, registrar=True):
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            self._rerenderizar(ids)
        finally:
            self.config(cursor="")
        if registrar:
            self._registrar_log(f"Moldear: {mensaje}")
        alto_grande = any(self.estado[i]["alto"] == 2 for i in ids)
        if alto_grande:
            self._construir_carriles()  # la lista de ediciones de la cabecera cambió
        else:
            self._redibujar()
        self._actualizar_botones()
        self._avisar(mensaje)

    def _avisar(self, texto):
        """Mensaje corto que se desvanece en la barra de herramientas."""
        self.etiqueta_sel.config(text=f"✓ {texto}", fg=tema.AZUL)
        pasos = 14

        def apagar(k=0):
            if not self.etiqueta_sel.winfo_exists():
                return
            if k >= pasos:
                self._actualizar_botones()
                return
            self.etiqueta_sel.config(fg=mezclar_color(tema.AZUL, tema.TEXTO_SUAVE, k / pasos))
            self.after(90, lambda: apagar(k + 1))

        self.after(900, apagar)

    def _actualizar_botones(self):
        if self.lienzo is None:
            return
        hay = bool(self.seleccion)
        for b in self._botones_tramo:
            b.habilitar(hay)
        self.boton_deshacer.habilitar(self.historial.puede_deshacer)
        self.boton_rehacer.habilitar(self.historial.puede_rehacer)
        if hay:
            i0, i1, ids = self.seleccion
            sr = self.info.muestreo
            nombres = ", ".join(p["etiqueta"] for p in self.pistas if p["id"] in ids)
            if len(nombres) > 22:
                nombres = f"{len(ids)} pistas"
            self.etiqueta_sel.config(
                text=f"Tramo {formato_tiempo(i0 / sr, True)} – {formato_tiempo(i1 / sr, True)} · {nombres}",
                fg=tema.TEXTO)
        else:
            self.etiqueta_sel.config(text="Arrastra sobre una pista para elegir un tramo", fg=tema.TEXTO_SUAVE)

    def _preparar_parametro(self):
        info = filtros_registry.obtener(self._efectos[self.var_efecto.get()])
        p = info["parametro"]
        if p is None:
            self.escala_param.state(["disabled"])
            self.etiqueta_param.config(text="")
            return
        self.escala_param.state(["!disabled"])
        self.escala_param.config(from_=p["min"], to=p["max"])
        self.var_param.set(p["default"])
        self._mostrar_param()

    def _mostrar_param(self):
        p = filtros_registry.obtener(self._efectos[self.var_efecto.get()])["parametro"]
        if p:
            self.etiqueta_param.config(text=f"{self.var_param.get():.{p.get('decimales', 2)}f}{p.get('unidad', '')}")

    # =====================================================================
    # Vista: coordenadas, zoom y desplazamiento
    # =====================================================================

    def _ancho_onda(self):
        return max(10, self.lienzo.winfo_width() - ANCHO_CABECERA)

    def _x(self, muestra):
        return ANCHO_CABECERA + (muestra - self._v0) / max(self._v1 - self._v0, 1) * self._ancho_onda()

    def _muestra(self, x):
        fr = (x - ANCHO_CABECERA) / self._ancho_onda()
        return int(min(max(self._v0 + fr * (self._v1 - self._v0), 0), self._largo()))

    def _fijar_vista(self, v0, v1):
        n = self._largo()
        ancho = min(max(v1 - v0, MIN_MUESTRAS_VISTA), n)
        v0 = min(max(v0, 0), n - ancho)
        self._v0, self._v1 = int(v0), int(v0 + ancho)
        self.barra_h.set(self._v0 / n, self._v1 / n)

    def zoom(self, factor, centro=None):
        if self.lienzo is None:
            return
        if centro is None:  # sin mouse: alrededor del cabezal si se ve, si no del centro
            pos = self.mezclador.posicion
            centro = pos if self._v0 <= pos <= self._v1 else (self._v0 + self._v1) // 2
        ancho = (self._v1 - self._v0) / factor
        fr = (centro - self._v0) / max(self._v1 - self._v0, 1)
        self._animar_vista(centro - fr * ancho, centro - fr * ancho + ancho)

    def zoom_ajustar(self):
        if self.lienzo is not None:
            self._animar_vista(0, self._largo())

    def _animar_vista(self, v0, v1):
        """El zoom se anima unos cuadros en vez de saltar de golpe."""
        desde = (self._v0, self._v1)
        if self._anim_zoom:
            self.after_cancel(self._anim_zoom)

        def paso(k=1):
            t = k / PASOS_ZOOM
            t = 1 - (1 - t) ** 3  # frena al final
            self._fijar_vista(desde[0] + (v0 - desde[0]) * t, desde[1] + (v1 - desde[1]) * t)
            self._redibujar()
            self._anim_zoom = self.after(16, lambda: paso(k + 1)) if k < PASOS_ZOOM else None

        paso()

    def _scroll_x(self, *args):
        n, ancho = self._largo(), self._v1 - self._v0
        if args[0] == "moveto":
            v0 = float(args[1]) * n
        else:
            cantidad, unidad = int(args[1]), args[2]
            v0 = self._v0 + cantidad * ancho * (0.9 if unidad == "pages" else 0.1)
        self._fijar_vista(v0, v0 + ancho)
        self._redibujar()

    def _rueda(self, evento):
        pasos = 1 if evento.delta > 0 else -1
        if evento.state & 0x0004:  # Ctrl: zoom alrededor del mouse
            self.zoom(1.5 if pasos > 0 else 1 / 1.5, centro=self._muestra(evento.x))
        elif evento.state & 0x0001:  # Shift: mover en el tiempo
            self._scroll_x("scroll", -pasos, "units")
        else:
            self.lienzo.yview_scroll(-pasos * 2, "units")

    # =====================================================================
    # Mouse: seleccionar tramos y mover el cabezal
    # =====================================================================

    def _pista_en(self, y_pantalla):
        y = self.lienzo.canvasy(y_pantalla)
        acumulado = 0
        visibles = self._visibles()
        for indice, p in enumerate(visibles):
            acumulado += ALTOS[self.estado[p["id"]]["alto"]]
            if y < acumulado:
                return indice
        return len(visibles) - 1

    def _presionar(self, evento):
        if evento.x < ANCHO_CABECERA:
            return
        self._arrastre = (self._muestra(evento.x), self._pista_en(evento.y), evento.x)

    def _arrastrar(self, evento):
        if not self._arrastre:
            return
        inicio, pista0, x0 = self._arrastre
        if abs(evento.x - x0) < 4:
            return
        if evento.x > self.lienzo.winfo_width() - 20:  # arrastrar contra el borde: avanzar la vista
            self._scroll_x("scroll", 1, "units")
        elif evento.x < ANCHO_CABECERA + 20 and self._v0 > 0:
            self._scroll_x("scroll", -1, "units")
        actual, pista1 = self._muestra(evento.x), self._pista_en(evento.y)
        visibles = self._visibles()
        a, b = sorted((pista0, pista1))
        self.seleccion = (min(inicio, actual), max(inicio, actual), [p["id"] for p in visibles[a:b + 1]])
        self._dibujar_seleccion()
        self._actualizar_botones()

    def _soltar(self, evento):
        if not self._arrastre:
            return
        _inicio, _pista, x0 = self._arrastre
        self._arrastre = None
        if abs(evento.x - x0) < 4:  # fue un click: mover el cabezal y quitar la selección
            self.mezclador.ir_a(self._muestra(evento.x))
            self.seleccion = None
            self._dibujar_seleccion()
            self._actualizar_botones()

    def _doble_click(self, evento):
        if evento.x < ANCHO_CABECERA:
            return
        pista = self._visibles()[self._pista_en(evento.y)]
        self.seleccion = (0, self._largo(), [pista["id"]])
        self._dibujar_seleccion()
        self._actualizar_botones()

    def seleccionar_todo(self):
        if self.lienzo is not None:
            self.seleccion = (0, self._largo(), [p["id"] for p in self._visibles()])
            self._dibujar_seleccion()
            self._actualizar_botones()

    def _click_regla(self, evento):
        self.mezclador.ir_a(self._muestra(evento.x))

    # =====================================================================
    # Dibujo
    # =====================================================================

    def _redibujar(self):
        if self.lienzo is None or not self.lienzo.winfo_exists():
            return
        c = self.lienzo
        c.delete("onda")
        ancho = self._ancho_onda()
        if self.barra_h is not None:
            n = self._largo()
            self.barra_h.set(self._v0 / n, self._v1 / n)
        hay_solo = any(self.estado[p["id"]]["solo"].get() for p in self._visibles())
        y = 0
        for pista in self._visibles():
            est = self.estado[pista["id"]]
            alto = ALTOS[est["alto"]]
            medio = y + alto / 2
            apagada = est["mute"].get() or (hay_solo and not est["solo"].get())
            color = mezclar_color(pista["color"], tema.FONDO, 0.65) if apagada else pista["color"]
            c.create_rectangle(ANCHO_CABECERA, y + 1, ANCHO_CABECERA + ancho, y + alto - 1,
                               fill=tema.ONDA_FONDO, outline="", tags="onda")
            # tramos editados: franja de color tenue
            for op in est["ops"]:
                if op["i1"] > self._v0 and op["i0"] < self._v1:
                    xa, xb = max(self._x(op["i0"]), ANCHO_CABECERA), min(self._x(op["i1"]), ANCHO_CABECERA + ancho)
                    c.create_rectangle(xa, y + 1, xb, y + alto - 1, outline="",
                                       fill=mezclar_color(pista["color"], tema.ONDA_FONDO, 0.82), tags="onda")
                    if xb - xa > 60 and alto > ALTOS[0]:
                        c.create_text(xa + 4, y + 4, anchor="nw", fill=mezclar_color(pista["color"], "#ffffff", 0.3),
                                      font=("TkDefaultFont", 7), tags="onda",
                                      text=ediciones.describir(op, self.info.muestreo))
            c.create_line(ANCHO_CABECERA, medio, ANCHO_CABECERA + ancho, medio, fill=tema.BORDE, tags="onda")
            # la onda, como un solo polígono (rápido aunque la pantalla sea ancha)
            columnas = int(ancho)
            minimos, maximos = est["picos"].columnas(self._v0, self._v1, columnas)
            escala = (alto / 2 - 6) / est["pico_max"] * min(est["vol"].get() / 100.0, 1.5)
            xs = ANCHO_CABECERA + np.arange(len(maximos)) * (ancho / max(len(maximos), 1))
            arriba = np.column_stack([xs, medio - maximos * escala]).ravel()
            abajo = np.column_stack([xs[::-1], (medio - minimos * escala)[::-1]]).ravel()
            c.create_polygon(*arriba, *abajo, fill=color, outline=color, tags="onda")
            c.create_line(ANCHO_CABECERA, y + alto, ANCHO_CABECERA + ancho, y + alto, fill=tema.BORDE, tags="onda")
            y += alto
        c.tag_raise("cabecera")
        self._dibujar_regla()
        self._dibujar_seleccion()
        self._dibujar_cabezal()

    def _dibujar_regla(self):
        r = self.regla
        r.delete("all")
        sr = self.info.muestreo
        ancho = self._ancho_onda()
        segundos = (self._v1 - self._v0) / sr
        paso = next((p for p in (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60) if segundos / p <= ancho / 80), 120)
        t = np.ceil(self._v0 / sr / paso) * paso
        while t * sr <= self._v1:
            x = self._x(t * sr)
            r.create_line(x, ALTO_REGLA - 8, x, ALTO_REGLA, fill=tema.TEXTO_SUAVE)
            r.create_text(x + 3, 4, anchor="nw", fill=tema.TEXTO_SUAVE, font=("TkDefaultFont", 8),
                          text=formato_tiempo(t, decimas=paso < 1))
            t += paso
        r.create_line(ANCHO_CABECERA, ALTO_REGLA - 1, ANCHO_CABECERA + ancho, ALTO_REGLA - 1, fill=tema.BORDE)
        r.create_text(10, ALTO_REGLA / 2, anchor="w", fill=tema.TEXTO_SUAVE, font=("TkDefaultFont", 8, "bold"),
                      text=f"PISTAS ({len(self._visibles())})")

    def _dibujar_seleccion(self):
        c = self.lienzo
        c.delete("sel")
        self.regla.delete("sel")
        if not self.seleccion:
            return
        i0, i1, ids = self.seleccion
        xa = max(self._x(i0), ANCHO_CABECERA)
        xb = min(self._x(i1), ANCHO_CABECERA + self._ancho_onda())
        if xb <= xa:
            return
        y = 0
        for pista in self._visibles():
            alto = ALTOS[self.estado[pista["id"]]["alto"]]
            if pista["id"] in ids:
                c.create_rectangle(xa, y + 1, xb, y + alto - 1, fill=tema.AZUL, stipple="gray25", outline=tema.AZUL,
                                   width=1, tags="sel")
            y += alto
        self.regla.create_rectangle(xa, 0, xb, ALTO_REGLA - 1, fill=mezclar_color(tema.AZUL, tema.PANEL, 0.6),
                                    outline="", tags="sel")
        self.regla.tag_lower("sel")
        c.tag_raise("cabecera")

    def _dibujar_cabezal(self):
        c = self.lienzo
        pos = self.mezclador.posicion if self.mezclador.largo else 0
        x = self._x(pos)
        visible = ANCHO_CABECERA <= x <= ANCHO_CABECERA + self._ancho_onda()
        alto = max(self._alto_total, c.winfo_height())
        if not c.find_withtag("cabezal"):
            c.create_line(0, 0, 0, 0, fill="#ffffff", width=2, tags="cabezal")
            self.regla.create_polygon(0, 0, 0, 0, 0, 0, fill="#ffffff", outline="", tags="cabezal")
        if visible:
            c.coords("cabezal", x, 0, x, alto)
            self.regla.coords("cabezal", x - 6, ALTO_REGLA - 10, x + 6, ALTO_REGLA - 10, x, ALTO_REGLA)
        else:
            c.coords("cabezal", -10, 0, -10, 0)
            self.regla.coords("cabezal", -10, 0, -10, 0, -10, 0)
        c.tag_raise("cabezal")
        self.regla.tag_raise("cabezal")
        c.tag_raise("cabecera")

    # =====================================================================
    # Reproducción
    # =====================================================================

    def _alternar_reproduccion(self):
        if self.info is None:
            return
        if self.mezclador.sonando:
            self.mezclador.pausar()
        else:
            self._configurar_mezclador()
            try:
                self.mezclador.iniciar()
            except reproductor.ErrorReproduccion as error:
                messagebox.showerror("No se pudo reproducir", str(error))

    def _detener(self):
        self.mezclador.detener()

    def _al_inicio(self):
        self.mezclador.ir_a(0)
        if self._v0 > 0:
            self._animar_vista(0, self._v1 - self._v0)

    def _cambiar_ab(self):
        self._suena_original = self.var_original.get()
        self._configurar_mezclador()

    def _tick(self):
        """~30 veces por segundo: cabezal, reloj, vúmetro y botón de play."""
        if not self.winfo_exists():
            return
        if self.info is not None and self.lienzo is not None and self.lienzo.winfo_exists():
            m = self.mezclador
            if m.termino and m.sonando:
                m.pausar()
            sonando = m.sonando
            pos = m.posicion
            # seguir al cabezal: si se sale de la pantalla mientras suena, la vista avanza
            if sonando and not self._arrastre and not (self._v0 <= pos < self._v1):
                ancho = self._v1 - self._v0
                self._fijar_vista(pos - ancho * 0.05, pos - ancho * 0.05 + ancho)
                self._redibujar()
            self._dibujar_cabezal()
            self.reloj.config(text=formato_tiempo(pos / self.info.muestreo, decimas=True))
            self.boton_play.cambiar_texto("❚❚" if sonando else "▶")
            nivel = m.nivel if sonando else (0.0, 0.0)
            self.medidor.actualizar(*nivel)
        self.after(MS_CUADRO, self._tick)

    # =====================================================================
    # Atajos de teclado (main.py los llama cuando esta pestaña está activa)
    # =====================================================================

    def tecla(self, nombre):
        if self.info is None or self.lienzo is None:
            return
        acciones = {
            "espacio": self._alternar_reproduccion,
            "suprimir": lambda: self._aplicar({"tipo": "silencio"}),
            "deshacer": self.deshacer,
            "rehacer": self.rehacer,
            "acercar": lambda: self.zoom(2.0),
            "alejar": lambda: self.zoom(0.5),
            "inicio": self._al_inicio,
            "todo": self.seleccionar_todo,
            "escape": lambda: (setattr(self, "seleccion", None), self._dibujar_seleccion(), self._actualizar_botones()),
        }
        if nombre in acciones:
            acciones[nombre]()

    # =====================================================================
    # Exportar
    # =====================================================================

    def _exportar_mezcla(self):
        if self.motor is None or self.info is None:
            return
        ruta = filedialog.asksaveasfilename(defaultextension=".wav", initialfile=f"{self.nombre}_mezcla.wav",
                                            filetypes=[("Audio WAV", "*.wav")])
        if not ruta:
            return
        renders = [self.estado[p["id"]]["render"] for p in self.pistas]
        ganancias, sr = self._ganancias(), self.info.muestreo
        mono = all(r[1] is None for r in renders)
        self.boton_exportar.habilitar(False)
        self.boton_exportar.cambiar_texto("Guardando...")

        def trabajo():
            try:
                t0 = time.perf_counter()
                izq, der = procesamiento.mezclar([(r[0], r[0] if r[1] is None else r[1]) for r in renders], ganancias)
                audio_io.guardar_wav(ruta, sr, izq, None if mono else der)
                fin = lambda: self._exportado(f"Mezcla guardada en:\n{ruta}",
                                              f"Mezcla exportada: {ruta} tiempo={time.perf_counter() - t0:.2f}s")
            except Exception as error:
                mensaje = str(error)
                fin = lambda: self._exportado(None, None, mensaje)
            self.after(0, fin)

        threading.Thread(target=trabajo, daemon=True).start()

    def _exportar_pistas(self):
        if self.motor is None or self.info is None:
            return
        carpeta = filedialog.askdirectory(title="Carpeta donde guardar cada pista")
        if not carpeta:
            return
        datos = [(p["id"], self.estado[p["id"]]["render"], self.estado[p["id"]]["vol"].get() / 100.0)
                 for p in self._visibles()]
        sr, nombre = self.info.muestreo, self.nombre
        self.boton_exportar_pistas.habilitar(False)
        self.boton_exportar_pistas.cambiar_texto("Guardando...")

        def trabajo():
            try:
                for id_p, (izq, der), vol in datos:
                    g = np.float32(vol)
                    audio_io.guardar_wav(os.path.join(carpeta, f"{nombre}_{id_p}.wav"), sr, izq * g,
                                         None if der is None else der * g)
                fin = lambda: self._exportado(f"{len(datos)} pistas guardadas en:\n{carpeta}",
                                              f"{len(datos)} pistas exportadas en {carpeta}")
            except Exception as error:
                mensaje = str(error)
                fin = lambda: self._exportado(None, None, mensaje)
            self.after(0, fin)

        threading.Thread(target=trabajo, daemon=True).start()

    def _exportado(self, aviso, registro, error=None):
        for boton, texto in ((self.boton_exportar, "⤓  Exportar mezcla"),
                             (self.boton_exportar_pistas, "⤓  Exportar pistas")):
            if boton.winfo_exists():
                boton.habilitar(True)
                boton.cambiar_texto(texto)
        if error:
            messagebox.showerror("Error al exportar", error)
            return
        self._registrar_log(registro)
        messagebox.showinfo("Exportado", aviso)


def _pintar_fondo(widget, color):
    try:
        if widget.winfo_class() in ("Frame", "Label", "Canvas") and widget.cget("bg") != color:
            widget.config(bg=color)
    except tk.TclError:
        return
    for hijo in widget.winfo_children():
        if hijo.winfo_class() in ("Frame", "Label", "Canvas"):
            _pintar_fondo(hijo, color)
