"""
main.py — ventana principal, Tkinter.

Paso 1: cargar/convertir/descargar el audio.
Panel interactivo: todo lo demás en una sola pantalla.
  - Pistas: separar la canción en voz, batería, bajo... y mezclarlas
    (volumen, silenciar, solo) mientras suena.
  - Efecto: a cada pista (o a la mezcla completa) se le puede aplicar un filtro.
  - Región: elegir qué parte del audio escuchar y previsualizar.
  - Exportar: la mezcla completa o cada pista por separado.
"""

import datetime
import os
import platform
import subprocess
import threading
import time
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import numpy as np

import audio_io
import filtros_registry
import graficas
import importador
import modo_datos
import procesamiento
import reproductor
import separador
import tema
from motor import ErrorMotor, MotorAudio

NOMBRE_APP = "Estudio de Audio"
RAIZ_PROYECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVO_LOG = os.path.join(RAIZ_PROYECTO, "registro.log")

OPCIONES_DURACION = ["8 s", "15 s", "30 s", "60 s", "Todo el audio"]  # largo de la región a previsualizar
MILISEGUNDOS_DEBOUNCE = 180     # espera tras mover un control antes de recalcular

COLOR_TEXTO_SUAVE = tema.TEXTO_SUAVE
COLOR_ERROR = tema.ERROR


class AppEstudioAudio:
    def __init__(self, root):
        self.root = root
        self.root.title(NOMBRE_APP)
        self.root.geometry("1100x720")
        self.root.minsize(980, 620)
        tema.aplicar(self.root)
        self.root.report_callback_exception = self._mostrar_excepcion_tk

        self.motor = None
        try:
            self.motor = MotorAudio(RAIZ_PROYECTO)
        except ErrorMotor as error:
            messagebox.showwarning(
                "Librería nativa no encontrada",
                f"{error}\n\nPodrás explorar la interfaz, pero no procesar audio hasta compilarla.",
            )

        # Estado de la sesión
        self.info = None
        self.izquierdo = None
        self.derecho = None
        self.historial = []
        self._nombre_base = "audio"
        self._sesion = 0  # sube con cada archivo nuevo o cambio de pantalla: descarta resultados viejos

        # Pistas (None = audio sin separar, se trata como una sola pista "mezcla")
        self.pistas = None
        self._pista_sel = "mezcla"
        self._filtro_por_pista = {}      # id_pista -> id_filtro
        self._valores_parametros = {}    # (id_pista, id_filtro) -> valor del control
        self._volumen = {}               # id_pista -> DoubleVar (0-150 %)
        self._silencio = {}              # id_pista -> BooleanVar
        self._solo = {}                  # id_pista -> BooleanVar
        self._etiquetas_volumen = {}
        self.var_pista_sel = tk.StringVar(value="mezcla")
        self._separando = False
        self._progreso = (0, 1)          # (muestras ya separadas, total)
        self._estado_separacion = ""     # mensaje al terminar o fallar la separación

        # Panel interactivo
        self.var_parametro = None
        self._etiqueta_valor = None
        self._info_parametro = None
        self._debounce_id = None
        self._suspender_auto_reproduccion = False
        self._mezclador = reproductor.Mezclador()
        self._audio_mezcla = None        # (pistas procesadas, ganancias) de la última vista previa
        self._ultimo_procesado = None    # (clave, pistas con filtro): evita recalcular si solo cambió el volumen
        self._reiniciar_audio = False

        # Región del audio que se previsualiza (y que se puede exportar sola)
        self.var_inicio = tk.DoubleVar(value=0.0)
        self.var_duracion = tk.StringVar(value=OPCIONES_DURACION[0])
        self.var_solo_region = tk.BooleanVar(value=False)

        self._crear_menu()

        self.contenedor = ttk.Frame(self.root)
        self.contenedor.pack(fill="both", expand=True)

        self._mostrar_paso_1()

        # Mientras el usuario elige o descarga la canción, se cargan los modelos de IA.
        threading.Thread(target=separador.precargar_modelos, daemon=True).start()

    # ---------- utilidades comunes ----------

    def _mostrar_excepcion_tk(self, tipo, valor, tb):
        """Tkinter se traga las excepciones de los callbacks y deja la ventana a medias.
        Esto las muestra en pantalla y las guarda en el registro."""
        detalle = "".join(traceback.format_exception(tipo, valor, tb))
        self._registrar_log("EXCEPCION EN LA INTERFAZ:\n" + detalle)
        messagebox.showerror("Error en la interfaz", detalle[-1500:])

    def _limpiar_contenedor(self):
        self._detener_audio()
        for widget in self.contenedor.winfo_children():
            widget.destroy()

    def _registrar_log(self, mensaje):
        linea = f"{datetime.datetime.now().isoformat(timespec='seconds')}  {mensaje}\n"
        try:
            with open(ARCHIVO_LOG, "a", encoding="utf-8") as f:
                f.write(linea)
        except OSError:
            pass  # el log es informativo; un fallo aquí no debe tumbar la app

    def _crear_menu(self):
        barra_menu = tk.Menu(self.root)

        menu_archivo = tk.Menu(barra_menu, tearoff=0)
        menu_archivo.add_command(label="Cargar archivo...", command=self._elegir_archivo)
        menu_archivo.add_separator()
        menu_archivo.add_command(label="Salir", command=self.root.quit)
        barra_menu.add_cascade(label="Archivo", menu=menu_archivo)

        menu_herramientas = tk.Menu(barra_menu, tearoff=0)
        menu_herramientas.add_command(label="Modo de datos (CSV)...", command=self._abrir_modo_datos)
        menu_herramientas.add_command(label="Ver registro de la sesión...", command=self._ver_registro)
        barra_menu.add_cascade(label="Herramientas", menu=menu_herramientas)

        menu_ayuda = tk.Menu(barra_menu, tearoff=0)
        menu_ayuda.add_command(label="Acerca de", command=self._acerca_de)
        barra_menu.add_cascade(label="Ayuda", menu=menu_ayuda)

        self.root.config(menu=barra_menu)

    def _abrir_modo_datos(self):
        if self.motor is None:
            messagebox.showwarning("Motor no disponible", "Compila la librería nativa primero.")
            return
        modo_datos.abrir_modo_datos(self.root, self.motor)

    def _ver_registro(self):
        ventana = tk.Toplevel(self.root)
        ventana.title("Registro de la sesión")
        ventana.geometry("640x400")
        texto = tk.Text(ventana, wrap="word")
        texto.pack(fill="both", expand=True, padx=10, pady=10)
        try:
            if os.path.exists(ARCHIVO_LOG):
                with open(ARCHIVO_LOG, encoding="utf-8") as f:
                    texto.insert("1.0", f.read())
        except OSError as error:
            texto.insert("1.0", f"No se pudo leer el registro: {error}")
        texto.config(state="disabled")

    def _acerca_de(self):
        messagebox.showinfo(
            NOMBRE_APP,
            f"{NOMBRE_APP}\nMotor de filtros en C++ (ctypes) + Python/Tkinter.\n"
            "Proyecto del curso Programación de Interfaces y Dispositivos Periféricos.",
        )

    # ---------- Paso 1: cargar archivo ----------

    def _mostrar_paso_1(self):
        self._sesion += 1  # si había una separación en curso, su resultado ya no aplica
        self._separando = False
        self._limpiar_contenedor()

        cuerpo = ttk.Frame(self.contenedor)
        cuerpo.pack(fill="both", expand=True, padx=28, pady=24)

        ttk.Label(cuerpo, text=NOMBRE_APP, style="Titulo.TLabel").pack(anchor="w")
        ttk.Label(
            cuerpo,
            text="Carga un archivo o pega un enlace — en cuanto esté listo entras directo al panel.",
            foreground=COLOR_TEXTO_SUAVE,
        ).pack(anchor="w", pady=(2, 16))

        marco_drop = ttk.LabelFrame(cuerpo, text="Archivo de audio")
        marco_drop.pack(fill="x", pady=(0, 14))
        ttk.Label(
            marco_drop,
            text="Acepta WAV, MP3, M4A, OGG, FLAC y similares (máximo 10 minutos). "
                 "Al cargarla, la canción se separa sola en voz, coros e instrumentos.",
        ).pack(anchor="w", padx=12, pady=(10, 4))

        fila_botones_origen = ttk.Frame(marco_drop)
        fila_botones_origen.pack(anchor="w", padx=12, pady=(0, 12))
        ttk.Button(fila_botones_origen, text="Seleccionar archivo...", command=self._elegir_archivo).pack(
            side="left"
        )
        ttk.Button(
            fila_botones_origen, text="Pegar enlace (YouTube u otro)...", command=self._importar_url
        ).pack(side="left", padx=(8, 0))

        self.etiqueta_info = ttk.Label(cuerpo, text="Ningún archivo cargado todavía.", foreground=COLOR_TEXTO_SUAVE)
        self.etiqueta_info.pack(anchor="w", pady=(0, 14))

        if self.historial:
            marco_hist = ttk.LabelFrame(cuerpo, text="Archivos de esta sesión")
            marco_hist.pack(fill="both", expand=True)
            for ruta in self.historial[-8:]:
                ttk.Label(marco_hist, text=os.path.basename(ruta)).pack(anchor="w", padx=12, pady=2)

    def _elegir_archivo(self):
        ruta = filedialog.askopenfilename(
            filetypes=[
                ("Audio y video", "*.wav *.mp3 *.m4a *.ogg *.flac *.aac *.mp4 *.mov *.mkv"),
                ("Todos los archivos", "*.*"),
            ]
        )
        if not ruta:
            return

        if ruta.lower().endswith(".wav"):
            self._cargar_ruta_wav(ruta, ruta_original=ruta)
            return

        self.etiqueta_info.config(text="Convirtiendo a WAV...", foreground=COLOR_TEXTO_SUAVE)
        self.root.update_idletasks()
        threading.Thread(target=self._convertir_en_hilo, args=(ruta,), daemon=True).start()

    def _convertir_en_hilo(self, ruta_origen):
        try:
            ruta_wav = importador.convertir_a_wav(ruta_origen)
        except importador.ErrorImportacion as error:
            mensaje = str(error)
            self.root.after(0, lambda: self._error_importacion(mensaje))
            return
        self.root.after(0, lambda: self._cargar_ruta_wav(ruta_wav, ruta_original=ruta_origen))

    def _error_importacion(self, error):
        if hasattr(self, "etiqueta_info"):
            self.etiqueta_info.config(text="No se pudo importar el archivo.", foreground=COLOR_ERROR)
        messagebox.showerror("Error al importar", str(error))

    def _importar_url(self):
        url = simpledialog.askstring(
            "Pegar enlace",
            "Enlace de YouTube u otro sitio soportado por yt-dlp:",
            parent=self.root,
        )
        if not url:
            return

        self.etiqueta_info.config(text="Descargando audio...", foreground=COLOR_TEXTO_SUAVE)
        self.root.update_idletasks()
        threading.Thread(target=self._descargar_en_hilo, args=(url,), daemon=True).start()

    def _descargar_en_hilo(self, url):
        def progreso(porcentaje):
            self.root.after(0, lambda: self.etiqueta_info.config(text=f"Descargando... {porcentaje}"))

        try:
            ruta_wav = importador.descargar_audio(
                url, callback_progreso=progreso, duracion_maxima_s=separador.DURACION_MAXIMA_S
            )
        except importador.ErrorImportacion as error:
            mensaje = str(error)
            self.root.after(0, lambda: self._error_importacion(mensaje))
            return
        self.root.after(0, lambda: self._cargar_ruta_wav(ruta_wav, ruta_original=url))

    def _cargar_ruta_wav(self, ruta_wav, ruta_original):
        try:
            info, izquierdo, derecho = audio_io.cargar_wav(ruta_wav)
            if info.duracion_segundos > separador.DURACION_MAXIMA_S:
                raise audio_io.ErrorFormatoAudio(
                    f"El audio dura {self._formato_tiempo(info.duracion_segundos)}; "
                    f"el máximo permitido es {separador.DURACION_MAXIMA_S // 60} minutos."
                )
        except audio_io.ErrorFormatoAudio as error:
            messagebox.showerror("Archivo no válido", str(error))
            if hasattr(self, "etiqueta_info"):
                self.etiqueta_info.config(text="Ningún archivo cargado todavía.", foreground=COLOR_TEXTO_SUAVE)
            return
        self.info, self.izquierdo, self.derecho = info, izquierdo, derecho

        if ruta_original not in self.historial:
            self.historial.append(ruta_original)
        self._registrar_log(f"Archivo cargado: {ruta_original}")
        self._nombre_base = (
            os.path.splitext(os.path.basename(ruta_original))[0] if os.path.exists(ruta_original) else "audio"
        )

        # Archivo nuevo: se empieza de cero (sin pistas, sin efectos, región al inicio).
        self._detener_audio()
        self._sesion += 1
        self._separando = False
        self.pistas = None
        self._pista_sel = "mezcla"
        self.var_pista_sel.set("mezcla")
        self._filtro_por_pista = {}
        self._valores_parametros = {}
        self._volumen, self._silencio, self._solo = {}, {}, {}
        self._asegurar_controles("mezcla")
        self.var_inicio.set(0.0)
        self._audio_mezcla = None
        self._estado_separacion = ""

        # Audio listo -> directo al panel interactivo; la separación arranca sola.
        self._mostrar_panel()
        self._iniciar_separacion()

    # ---------- pistas: datos ----------

    def _asegurar_controles(self, id_pista):
        if id_pista not in self._volumen:
            self._volumen[id_pista] = tk.DoubleVar(value=100.0)
            self._silencio[id_pista] = tk.BooleanVar(value=False)
            self._solo[id_pista] = tk.BooleanVar(value=False)

    def _lista_pistas(self):
        """Las pistas separadas, o una sola pista "mezcla" con el audio original."""
        if self.pistas:
            return self.pistas
        return [{"id": "mezcla", "etiqueta": "Audio", "color": tema.ROJO, "izq": self.izquierdo, "der": self.derecho}]

    def _pista_actual(self):
        for pista in self._lista_pistas():
            if pista["id"] == self._pista_sel:
                return pista
        return self._lista_pistas()[0]

    def _valor_parametro(self, id_pista, id_filtro):
        info = filtros_registry.obtener(id_filtro)
        if info["parametro"] is None:
            return 0.0
        return self._valores_parametros.get((id_pista, id_filtro), info["parametro"]["default"])

    def _snapshot(self):
        """Foto de todo lo que decide el resultado (pista, filtro, valor, volumen, silencio/solo).
        Se toma en el hilo de la interfaz para poder usarla después en otro hilo sin tocar Tk."""
        pistas = self._lista_pistas()
        hay_solo = any(self._solo[p["id"]].get() for p in pistas)
        elementos = []
        for pista in pistas:
            id_p = pista["id"]
            id_f = self._filtro_por_pista.get(id_p, "ninguno")
            volumen = self._volumen[id_p].get() / 100.0
            activa = (not hay_solo or self._solo[id_p].get()) and not self._silencio[id_p].get()
            elementos.append({
                "pista": pista,
                "info": filtros_registry.obtener(id_f),
                "valor": self._valor_parametro(id_p, id_f),
                "vol": volumen,
                "ganancia": volumen if activa else 0.0,
            })
        return elementos

    def _procesar_snapshot(self, elementos, i0, i1):
        """Aplica el filtro de cada pista a [i0:i1]. Devuelve [(izq, der), ...] (der nunca es None)."""
        salida = []
        for e in elementos:
            pista = e["pista"]
            izq = pista["izq"][i0:i1]
            der = pista["der"][i0:i1] if pista["der"] is not None else None
            res_izq, res_der = procesamiento.aplicar_filtro(self.motor, e["info"], e["valor"], izq, der)
            salida.append((res_izq, res_izq if res_der is None else res_der))
        return salida

    # ---------- región del audio ----------

    @staticmethod
    def _formato_tiempo(segundos):
        segundos = int(round(segundos))
        return f"{segundos // 60}:{segundos % 60:02d}"

    def _duracion_total(self):
        return len(self.izquierdo) / float(self.info.muestreo)

    def _segundos_region(self):
        """Largo elegido en segundos, o None si es 'Todo el audio'."""
        texto = self.var_duracion.get()
        if texto == "Todo el audio":
            return None
        return float(texto.split()[0])

    def _limites_region(self):
        """Devuelve (i0, i1): muestras donde empieza y termina la región elegida."""
        n = len(self.izquierdo)
        dur = self._segundos_region()
        if dur is None:
            return 0, n
        sr = self.info.muestreo
        max_inicio = max(0.0, self._duracion_total() - dur)
        inicio = min(max(self.var_inicio.get(), 0.0), max_inicio)
        i0 = int(inicio * sr)
        return i0, min(n, i0 + int(dur * sr))

    def _configurar_escala_inicio(self):
        dur = self._segundos_region()
        if dur is None:
            self.var_inicio.set(0.0)
            self._escala_inicio.config(to=1.0)
            self._escala_inicio.state(["disabled"])
        else:
            max_inicio = max(0.0, self._duracion_total() - dur)
            self._escala_inicio.config(to=max(max_inicio, 0.01))
            if self.var_inicio.get() > max_inicio:
                self.var_inicio.set(max_inicio)
            self._escala_inicio.state(["!disabled"] if max_inicio > 0 else ["disabled"])
        self._actualizar_etiqueta_region()

    def _actualizar_etiqueta_region(self):
        i0, i1 = self._limites_region()
        sr = self.info.muestreo
        self.etiqueta_region.config(
            text=f"{self._formato_tiempo(i0 / sr)} – {self._formato_tiempo(i1 / sr)}  "
                 f"(de {self._formato_tiempo(self._duracion_total())})"
        )

    def _on_cambio_duracion(self):
        self._configurar_escala_inicio()
        self._reiniciar_audio = True
        self._programar_vista_previa()

    def _on_cambio_inicio(self):
        self._actualizar_etiqueta_region()
        self._reiniciar_audio = True
        self._programar_vista_previa()

    # ---------- panel interactivo ----------

    def _mostrar_panel(self):
        self._limpiar_contenedor()

        barra = ttk.Frame(self.contenedor)
        barra.pack(fill="x", padx=18, pady=(14, 6))
        ttk.Label(barra, text=NOMBRE_APP, style="Titulo.TLabel").pack(side="left")
        ttk.Label(barra, text=f"  ·  {self.info.resumen()}", foreground=COLOR_TEXTO_SUAVE).pack(side="left")
        ttk.Button(barra, text="Cargar otro archivo", command=self._mostrar_paso_1).pack(side="right")
        ttk.Separator(self.contenedor).pack(fill="x")

        pie = ttk.Frame(self.contenedor)
        pie.pack(fill="x", padx=18, pady=14, side="bottom")
        self.boton_exportar = ttk.Button(
            pie, text="Exportar mezcla como WAV...", style="Acento.TButton", command=self._exportar_mezcla
        )
        self.boton_exportar.pack(side="right")
        self.boton_exportar_pistas = ttk.Button(
            pie, text="Exportar cada pista por separado...", command=self._exportar_pistas
        )
        self.boton_exportar_pistas.pack(side="right", padx=(0, 10))
        self._actualizar_botones_exportar()

        cuerpo = ttk.Frame(self.contenedor)
        cuerpo.pack(fill="both", expand=True, padx=18, pady=12)

        # ----- columna izquierda: pistas (arriba) y efecto de la pista elegida (abajo) -----
        columna_izq = ttk.Frame(cuerpo)
        columna_izq.pack(side="left", fill="y", padx=(0, 14))
        self._marco_pistas = ttk.LabelFrame(columna_izq, text="Pistas")
        self._marco_pistas.pack(side="top", fill="x")
        self._marco_filtros = ttk.LabelFrame(columna_izq, text="Efecto")
        self._marco_filtros.pack(side="top", fill="both", expand=True, pady=(10, 0))
        lista = tema.PanelDesplazable(self._marco_filtros, ancho=250)
        lista.pack(fill="both", expand=True, padx=4, pady=6)
        self._contenedor_filtros = lista.interior

        # ----- columna derecha -----
        columna_der = ttk.Frame(cuerpo)
        columna_der.pack(side="left", fill="both", expand=True)

        # Lo de tamaño fijo se empaqueta primero y de abajo hacia arriba; la gráfica
        # va al final y es la que se encoge si la ventana es chica (así nada se corta).
        if not reproductor.disponible():
            ttk.Label(
                columna_der,
                text="(Falta el paquete sounddevice para escuchar en vivo: pip install sounddevice)",
                foreground=COLOR_TEXTO_SUAVE,
            ).pack(side="bottom", anchor="w")

        self.etiqueta_estado_previa = ttk.Label(columna_der, text="", foreground=COLOR_TEXTO_SUAVE)
        self.etiqueta_estado_previa.pack(side="bottom", anchor="w", pady=(4, 0))

        fila_reproduccion = ttk.Frame(columna_der)
        fila_reproduccion.pack(side="bottom", fill="x", pady=(8, 0))
        ttk.Button(fila_reproduccion, text="▶ Escuchar mezcla", command=self._escuchar_mezcla).pack(side="left")
        ttk.Button(fila_reproduccion, text="▶ Original", command=self._escuchar_original).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(fila_reproduccion, text="■ Detener", command=self._detener_audio).pack(side="left", padx=(8, 0))

        self.var_autoreproducir = tk.BooleanVar(value=reproductor.disponible())
        check_auto = ttk.Checkbutton(
            fila_reproduccion, text="Escuchar al mover un control", variable=self.var_autoreproducir
        )
        check_auto.pack(side="left", padx=(16, 0))
        if not reproductor.disponible():
            check_auto.config(state="disabled")
            self.var_autoreproducir.set(False)

        marco_region = ttk.LabelFrame(columna_der, text="Región del audio a escuchar y previsualizar")
        marco_region.pack(side="bottom", fill="x", pady=(8, 0))
        fila_region = ttk.Frame(marco_region)
        fila_region.pack(fill="x", padx=10, pady=(6, 2))
        ttk.Label(fila_region, text="Duración:").pack(side="left")
        combo_duracion = ttk.Combobox(
            fila_region, textvariable=self.var_duracion, values=OPCIONES_DURACION, state="readonly", width=13
        )
        combo_duracion.pack(side="left", padx=(6, 16))
        combo_duracion.bind("<<ComboboxSelected>>", lambda evento: self._on_cambio_duracion())
        ttk.Label(fila_region, text="Inicio:").pack(side="left")
        self._escala_inicio = ttk.Scale(
            fila_region, from_=0.0, to=1.0, variable=self.var_inicio, orient="horizontal",
            command=lambda valor: self._on_cambio_inicio(),
        )
        self._escala_inicio.pack(side="left", fill="x", expand=True, padx=(6, 10))
        self.etiqueta_region = ttk.Label(fila_region, text="", width=22, foreground=COLOR_TEXTO_SUAVE)
        self.etiqueta_region.pack(side="left")
        ttk.Checkbutton(
            marco_region,
            text="Al exportar la mezcla, aplicar los cambios solo a esta región (el resto queda igual)",
            variable=self.var_solo_region,
        ).pack(anchor="w", padx=10, pady=(0, 6))

        marco_grafica = ttk.Frame(columna_der)
        marco_grafica.pack(side="top", fill="both", expand=True)
        self._grafica = graficas.GraficaPistas(marco_grafica)
        self._grafica.widget.pack(fill="both", expand=True)
        self._ultimo_procesado = None

        self._configurar_escala_inicio()
        self._construir_panel_pistas()
        self._construir_lista_filtros()
        self._refrescar_vista_sin_sonar()  # no sonar nada al entrar al panel

    # ---------- panel de pistas (separar y mezclar) ----------

    def _construir_panel_pistas(self):
        for widget in self._marco_pistas.winfo_children():
            widget.destroy()
        self._etiquetas_volumen = {}

        self._barra_progreso = None
        self._etiqueta_progreso = None
        if self._separando:
            self._etiqueta_progreso = ttk.Label(
                self._marco_pistas, text="", wraplength=250, justify="left", foreground=COLOR_TEXTO_SUAVE
            )
            self._etiqueta_progreso.pack(anchor="w", padx=10, pady=(8, 2))
            self._barra_progreso = ttk.Progressbar(self._marco_pistas, mode="determinate", maximum=1000)
            self._barra_progreso.pack(fill="x", padx=10, pady=(0, 4))
            self._actualizar_progreso()
        elif self._estado_separacion:
            ttk.Label(
                self._marco_pistas, text=self._estado_separacion, wraplength=250, justify="left",
                foreground=COLOR_TEXTO_SUAVE,
            ).pack(anchor="w", padx=10, pady=(8, 2))

        for pista in self.pistas or []:
            self._fila_pista(pista)
        ttk.Frame(self._marco_pistas, height=8).pack()

    def _fila_pista(self, pista):
        id_p = pista["id"]
        fila = ttk.Frame(self._marco_pistas)
        fila.pack(fill="x", padx=10, pady=(6, 0))

        arriba = ttk.Frame(fila)
        arriba.pack(fill="x")
        tk.Frame(arriba, width=10, height=10, bg=pista["color"]).pack(side="left", padx=(0, 6))
        ttk.Radiobutton(
            arriba, text=pista["etiqueta"], value=id_p, variable=self.var_pista_sel,
            command=lambda i=id_p: self._seleccionar_pista(i),
        ).pack(side="left")
        ttk.Checkbutton(
            arriba, text="S", width=2, style="Solo.Toolbutton", variable=self._solo[id_p],
            command=self._on_mezcla_cambio,
        ).pack(side="right", padx=(3, 0))
        ttk.Checkbutton(
            arriba, text="M", width=2, style="Mute.Toolbutton", variable=self._silencio[id_p],
            command=self._on_mezcla_cambio,
        ).pack(side="right")

        abajo = ttk.Frame(fila)
        abajo.pack(fill="x")
        ttk.Scale(
            abajo, from_=0, to=150, variable=self._volumen[id_p], orient="horizontal",
            command=lambda valor: self._on_mezcla_cambio(),
        ).pack(side="left", fill="x", expand=True)
        etiqueta = ttk.Label(abajo, text="100%", width=5, anchor="e", foreground=COLOR_TEXTO_SUAVE)
        etiqueta.pack(side="left", padx=(6, 0))
        self._etiquetas_volumen[id_p] = etiqueta

    def _seleccionar_pista(self, id_pista):
        self._pista_sel = id_pista
        self._construir_lista_filtros()

    def _on_mezcla_cambio(self):
        """Cambió un volumen, un M o un S: el sonido cambia al instante; la gráfica se redibuja
        al soltar el control (redibujar en cada movimiento del mouse trababa la interfaz)."""
        elementos = self._snapshot()
        for e in elementos:
            etiqueta = self._etiquetas_volumen.get(e["pista"]["id"])
            if etiqueta is not None:
                etiqueta.config(text=f"{round(e['vol'] * 100)}%")
        if reproductor.disponible() and self._mezclador.cantidad_pistas == len(elementos):
            self._mezclador.cambiar_ganancias([e["ganancia"] for e in elementos])
        self._programar_vista_previa(espera_ms=400)

    # ---------- separación automática (arranca sola al cargar el audio) ----------

    def _iniciar_separacion(self):
        instrucciones = separador.falta_instalar()
        if instrucciones:
            self._estado_separacion = "La separación con IA no está instalada."
            self._construir_panel_pistas()
            messagebox.showinfo("Separación con IA", instrucciones)
            return

        self._separando = True
        self._progreso = (0, len(self.izquierdo))
        self._inicio_separacion = time.perf_counter()
        self._actualizar_botones_exportar()
        self._construir_panel_pistas()
        self._tick_progreso()

        threading.Thread(
            target=self._separar_en_hilo,
            args=(self._sesion, self.izquierdo, self.derecho, self.info.muestreo),
            daemon=True,
        ).start()

    def _separar_en_hilo(self, sesion, izquierdo, derecho, muestreo):
        def avance(pistas, hecho, total):
            self.root.after(0, lambda: self._on_avance_separacion(sesion, pistas, hecho, total))

        try:
            clave = separador.clave_audio(izquierdo, derecho, muestreo)
            pistas = separador.cargar_de_cache(clave)
            desde_cache = pistas is not None
            if not desde_cache:
                pistas = separador.separar_por_partes(
                    izquierdo, derecho, muestreo, al_avanzar=avance, cancelado=lambda: sesion != self._sesion
                )
                if pistas is None:
                    return  # cancelada: se cargó otro archivo
                separador.guardar_en_cache(clave, pistas)
        except Exception as error:  # cualquier fallo del modelo: avisar, no cerrar la app
            mensaje = str(error) if isinstance(error, separador.ErrorSeparacion) else f"Error inesperado:\n{error}"
            self.root.after(0, lambda: self._separacion_fallida(sesion, mensaje))
            return
        self.root.after(0, lambda: self._separacion_lista(sesion, pistas, desde_cache))

    def _tick_progreso(self):
        """Actualiza el reloj de la separación cada medio segundo."""
        if not self._separando:
            return
        self._actualizar_progreso()
        self.root.after(500, self._tick_progreso)

    def _actualizar_progreso(self):
        if self._etiqueta_progreso is None:
            return
        hecho, total = self._progreso
        sr = self.info.muestreo
        transcurrido = time.perf_counter() - self._inicio_separacion
        texto = (
            f"Separando con IA... {self._formato_tiempo(hecho / sr)} de {self._formato_tiempo(total / sr)} "
            f"listo ({self._formato_tiempo(transcurrido)}). Ya puedes escuchar y editar lo separado."
            if hecho else
            f"Separando con IA... la primera parte estará lista en unos segundos "
            f"({self._formato_tiempo(transcurrido)})."
        )
        try:
            self._etiqueta_progreso.config(text=texto)
            self._barra_progreso.config(value=1000 * hecho / max(total, 1))
        except tk.TclError:
            pass  # el panel se está reconstruyendo

    def _on_avance_separacion(self, sesion, pistas, hecho, total):
        """Llegó un trozo nuevo: actualiza progreso, pistas visibles y (si hace falta) la vista previa."""
        if sesion != self._sesion or not self._separando:
            return
        anterior = self._progreso[0]
        self._progreso = (hecho, total)
        if self._aplicar_pistas(pistas):
            self._refrescar_vista_sin_sonar()
            return
        self._actualizar_progreso()
        i0, i1 = self._limites_region()
        if i0 < hecho and i1 > anterior:  # el trozo nuevo cae dentro de la región que se está viendo
            self._refrescar_vista_sin_sonar()

    def _aplicar_pistas(self, pistas):
        """Muestra estas pistas si cambió cuáles son. Devuelve True si reconstruyó el panel."""
        ids = [p["id"] for p in pistas]
        if ids == [p["id"] for p in self.pistas or []]:
            self.pistas = pistas
            return False
        self.pistas = pistas or None
        for id_p in ids:
            self._asegurar_controles(id_p)
        if self._pista_sel not in ids:
            self._pista_sel = ids[0] if ids else "mezcla"
            self.var_pista_sel.set(self._pista_sel)
        self._construir_panel_pistas()
        self._construir_lista_filtros()
        return True

    def _separacion_fallida(self, sesion, mensaje):
        if sesion != self._sesion:
            return
        self._separando = False
        self._estado_separacion = "No se pudo separar la canción."
        self._actualizar_botones_exportar()
        self._construir_panel_pistas()
        messagebox.showerror("No se pudo separar", mensaje)

    def _separacion_lista(self, sesion, pistas, desde_cache):
        if sesion != self._sesion:
            return  # mientras tanto se cargó otro archivo o se cambió de pantalla
        self._separando = False
        segundos = time.perf_counter() - self._inicio_separacion
        nombres = ", ".join(p["etiqueta"] for p in pistas)
        if desde_cache:
            self._estado_separacion = "Pistas recuperadas de una separación anterior."
        else:
            self._estado_separacion = f"Separada en {self._formato_tiempo(segundos)}."
            self._registrar_log(f"Pistas separadas en {segundos:.1f}s: {nombres}")
        self._aplicar_pistas(pistas)
        self._construir_panel_pistas()
        self._actualizar_botones_exportar()
        self._refrescar_vista_sin_sonar()

    def _actualizar_botones_exportar(self):
        """Exportar espera a que termine la separación (si no, faltaría parte de las pistas)."""
        if self._separando:
            self.boton_exportar.config(state="disabled", text="Exportar (esperando separación)")
            self.boton_exportar_pistas.config(state="disabled")
            return
        self.boton_exportar.config(
            state="normal" if self.motor is not None else "disabled", text="Exportar mezcla como WAV..."
        )
        self.boton_exportar_pistas.config(state="normal" if self.pistas else "disabled")

    def _refrescar_vista_sin_sonar(self):
        """Recalcula y redibuja la vista previa sin arrancar la reproducción por su cuenta."""
        self._suspender_auto_reproduccion = True
        self._generar_vista_previa(forzar=True)
        self._suspender_auto_reproduccion = False

    # ---------- efecto de la pista elegida ----------

    def _construir_lista_filtros(self):
        for widget in self._contenedor_filtros.winfo_children():
            widget.destroy()

        pista = self._pista_actual()
        self._marco_filtros.config(text=f"Efecto para: {pista['etiqueta']}" if self.pistas else "Efecto")
        actual = self._filtro_por_pista.get(self._pista_sel, "ninguno")
        self.var_filtro = tk.StringVar(value=actual)
        self._marcos_parametro = {}

        for id_filtro, etiqueta in filtros_registry.listar():
            info_filtro = filtros_registry.obtener(id_filtro)

            marco_item = ttk.Frame(self._contenedor_filtros)
            marco_item.pack(fill="x", pady=(0, 10))

            ttk.Radiobutton(
                marco_item, text=etiqueta, value=id_filtro, variable=self.var_filtro,
                command=lambda: self._seleccionar_filtro(self.var_filtro.get()),
            ).pack(anchor="w")
            ttk.Label(
                marco_item, text=info_filtro["descripcion"], foreground=COLOR_TEXTO_SUAVE,
                wraplength=230, justify="left",
            ).pack(anchor="w", padx=(20, 0))

            marco_parametro = ttk.Frame(marco_item)
            marco_parametro.pack(fill="x", padx=(20, 0), pady=(4, 0))
            self._marcos_parametro[id_filtro] = marco_parametro

        if not self._marcos_parametro:
            ttk.Label(
                self._contenedor_filtros,
                text="No hay filtros disponibles.\nRevisa app/filtros_registry.py",
                foreground=COLOR_ERROR, justify="left",
            ).pack(anchor="w")

        if actual in self._marcos_parametro:
            self._construir_control_parametro(actual)

    def _seleccionar_filtro(self, id_filtro):
        self._filtro_por_pista[self._pista_sel] = id_filtro
        self._construir_control_parametro(id_filtro)
        self._generar_vista_previa()

    @staticmethod
    def _texto_parametro(p, valor):
        return f"{p['nombre']}: {valor:.{p.get('decimales', 2)}f}{p.get('unidad', '')}"

    def _construir_control_parametro(self, id_filtro):
        # Limpia el slot de parámetro de todos los filtros (solo el elegido muestra el suyo).
        for marco in self._marcos_parametro.values():
            for widget in marco.winfo_children():
                widget.destroy()

        info_filtro = filtros_registry.obtener(id_filtro)
        marco = self._marcos_parametro[id_filtro]

        if info_filtro["parametro"] is None:
            self.var_parametro = None
            self._etiqueta_valor = None
            self._info_parametro = None
            return

        p = info_filtro["parametro"]
        valor = self._valor_parametro(self._pista_sel, id_filtro)
        self.var_parametro = tk.DoubleVar(value=valor)
        self._info_parametro = p
        fila = ttk.Frame(marco)
        fila.pack(fill="x")
        self._etiqueta_valor = ttk.Label(fila, text=self._texto_parametro(p, valor), font=("TkDefaultFont", 9))
        self._etiqueta_valor.pack(anchor="w")
        ttk.Scale(
            fila, from_=p["min"], to=p["max"], variable=self.var_parametro, orient="horizontal",
            command=lambda v: self._on_param_change(v),
        ).pack(fill="x")

    def _on_param_change(self, valor):
        id_filtro = self._filtro_por_pista.get(self._pista_sel, "ninguno")
        self._valores_parametros[(self._pista_sel, id_filtro)] = float(valor)
        if self._etiqueta_valor is not None and self._info_parametro is not None:
            self._etiqueta_valor.config(text=self._texto_parametro(self._info_parametro, float(valor)))
        self._programar_vista_previa()

    # ---------- vista previa ----------

    def _programar_vista_previa(self, espera_ms=MILISEGUNDOS_DEBOUNCE):
        if self._debounce_id is not None:
            self.root.after_cancel(self._debounce_id)
        self._debounce_id = self.root.after(espera_ms, self._generar_vista_previa)

    def _generar_vista_previa(self, forzar=False):
        """forzar=True recalcula los filtros aunque nada haya cambiado (p. ej. porque
        la separación llenó un trozo nuevo de las pistas)."""
        self._debounce_id = None
        if self.motor is None or self.info is None:
            return

        i0, i1 = self._limites_region()
        elementos = self._snapshot()
        # Si solo cambió un volumen/M/S, los filtros dan lo mismo que antes: se reutiliza.
        clave = (i0, i1, tuple((e["pista"]["id"], id(e["pista"]["izq"]), id(e["info"]), e["valor"]) for e in elementos))
        inicio = time.perf_counter()
        if not forzar and self._ultimo_procesado is not None and self._ultimo_procesado[0] == clave:
            procesadas = self._ultimo_procesado[1]
        else:
            try:
                procesadas = self._procesar_snapshot(elementos, i0, i1)
            except Exception as error:
                self.etiqueta_estado_previa.config(
                    text=f"Error al generar la vista previa: {error}", foreground=COLOR_ERROR
                )
                return
            self._ultimo_procesado = (clave, procesadas)
        duracion = time.perf_counter() - inicio
        sr = self.info.muestreo

        if self.pistas:
            carriles = []
            for e, (izq, _der) in zip(elementos, procesadas):
                escala = e["vol"] if e["vol"] > 0 else 1.0  # las silenciadas se ven (atenuadas), no desaparecen
                carriles.append((
                    e["pista"]["etiqueta"].upper(), e["pista"]["color"],
                    izq * np.float32(escala), e["ganancia"] == 0.0,
                ))
            escala_comun = True
        else:
            carriles = [
                ("ORIGINAL", tema.AZUL, self.izquierdo[i0:i1], False),
                ("PROCESADO", tema.ROJO, procesadas[0][0], False),
            ]
            escala_comun = False

        self._grafica.dibujar(carriles, sr, inicio_s=i0 / sr, escala_comun=escala_comun)

        self.etiqueta_estado_previa.config(
            text=f"Vista previa actualizada ({duracion * 1000:.1f} ms) · región {self.etiqueta_region.cget('text')}",
            foreground=COLOR_TEXTO_SUAVE,
        )
        self._configurar_audio(procesadas, [e["ganancia"] for e in elementos])

    # ---------- reproducción ----------

    def _configurar_audio(self, procesadas, ganancias):
        """Deja lista la mezcla para escucharla. Si ya está sonando, el cambio se oye sin cortar."""
        self._audio_mezcla = (procesadas, ganancias)
        if not reproductor.disponible():
            return
        self._mezclador.configurar(self.info.muestreo, procesadas, ganancias, reiniciar=self._reiniciar_audio)
        self._reiniciar_audio = False
        if (
            self.var_autoreproducir.get()
            and not self._suspender_auto_reproduccion
            and not self._mezclador.sonando
        ):
            self._iniciar_audio()

    def _iniciar_audio(self):
        try:
            self._mezclador.iniciar()
        except reproductor.ErrorReproduccion as error:
            messagebox.showerror("No se pudo reproducir", str(error))

    def _detener_audio(self):
        self._mezclador.detener()

    def _escuchar_mezcla(self):
        if self._audio_mezcla is None:
            return
        procesadas, ganancias = self._audio_mezcla
        if reproductor.disponible():
            self._mezclador.configurar(self.info.muestreo, procesadas, ganancias, reiniciar=True)
            self._iniciar_audio()
        else:
            izq, der = procesamiento.mezclar(procesadas, ganancias)
            self._reproducir_con_el_sistema(izq, der)

    def _escuchar_original(self):
        if self.info is None:
            return
        i0, i1 = self._limites_region()
        izq = self.izquierdo[i0:i1]
        der = self.derecho[i0:i1] if self.derecho is not None else None
        if reproductor.disponible():
            self._mezclador.configurar(self.info.muestreo, [(izq, der)], [1.0], reiniciar=True)
            self._iniciar_audio()
        else:
            self._reproducir_con_el_sistema(izq, der)

    def _reproducir_con_el_sistema(self, izquierdo, derecho):
        """Respaldo si no hay sounddevice: usa el reproductor de Windows (no es en vivo)."""
        ruta_temporal = os.path.join(RAIZ_PROYECTO, "_reproduccion_temp.wav")
        try:
            audio_io.guardar_wav(ruta_temporal, self.info.muestreo, izquierdo, derecho)
            sistema = platform.system()
            if sistema == "Windows":
                os.startfile(ruta_temporal)  # noqa: abre con el reproductor por defecto de Windows
            elif sistema == "Darwin":
                subprocess.Popen(["afplay", ruta_temporal])
            else:
                subprocess.Popen(["xdg-open", ruta_temporal])
        except Exception as error:
            messagebox.showerror("No se pudo reproducir", f"No se pudo reproducir el audio:\n{error}")

    # ---------- exportar ----------

    def _exportar_mezcla(self):
        if self.motor is None:
            messagebox.showwarning("Motor no disponible", "Compila la librería nativa primero.")
            return
        ruta = filedialog.asksaveasfilename(defaultextension=".wav", filetypes=[("Audio WAV", "*.wav")])
        if not ruta:
            return

        i0, i1 = self._limites_region()
        n = len(self.izquierdo)
        solo_region = self.var_solo_region.get() and (i0, i1) != (0, n)
        sr = self.info.muestreo
        detalle = (
            f"solo región {self._formato_tiempo(i0 / sr)}–{self._formato_tiempo(i1 / sr)}"
            if solo_region else "audio completo"
        )
        elementos = self._snapshot()
        mono_de_origen = self.derecho is None and not self.pistas

        self.boton_exportar.config(state="disabled", text="Procesando audio completo...")
        self.root.update_idletasks()
        threading.Thread(
            target=self._exportar_mezcla_en_hilo,
            args=(ruta, elementos, solo_region, i0, i1, mono_de_origen, detalle),
            daemon=True,
        ).start()

    def _exportar_mezcla_en_hilo(self, ruta, elementos, solo_region, i0, i1, mono_de_origen, detalle):
        try:
            inicio = time.perf_counter()
            desde, hasta = (i0, i1) if solo_region else (0, len(self.izquierdo))
            procesadas = self._procesar_snapshot(elementos, desde, hasta)
            izq, der = procesamiento.mezclar(procesadas, [e["ganancia"] for e in elementos])
            if solo_region:
                # Fuera de la región queda el audio original, sin tocar.
                salida_izq = self.izquierdo.copy()
                salida_der = (self.derecho if self.derecho is not None else self.izquierdo).copy()
                salida_izq[desde:hasta] = izq
                salida_der[desde:hasta] = der
                izq, der = salida_izq, salida_der
            audio_io.guardar_wav(ruta, self.info.muestreo, izq, None if mono_de_origen else der)
            duracion = time.perf_counter() - inicio
        except Exception as error:
            mensaje = str(error)
            self.root.after(0, lambda: self._error_exportando(mensaje))
            return
        self.root.after(0, lambda: self._exportado_listo(ruta, detalle, duracion))

    def _exportar_pistas(self):
        if not self.pistas or self.motor is None:
            return
        carpeta = filedialog.askdirectory(title="Carpeta donde guardar cada pista")
        if not carpeta:
            return
        elementos = self._snapshot()
        self.boton_exportar_pistas.config(state="disabled", text="Guardando pistas...")
        self.root.update_idletasks()
        threading.Thread(target=self._exportar_pistas_en_hilo, args=(carpeta, elementos), daemon=True).start()

    def _exportar_pistas_en_hilo(self, carpeta, elementos):
        try:
            inicio = time.perf_counter()
            n = len(self.izquierdo)
            for e in elementos:
                izq, der = self._procesar_snapshot([e], 0, n)[0]
                volumen = np.float32(e["vol"])  # cada pista sale con su filtro y su volumen (sin silenciar/solo)
                ruta = os.path.join(carpeta, f"{self._nombre_base}_{e['pista']['id']}.wav")
                audio_io.guardar_wav(ruta, self.info.muestreo, izq * volumen, der * volumen)
            duracion = time.perf_counter() - inicio
        except Exception as error:
            mensaje = str(error)
            self.root.after(0, lambda: self._error_exportando(mensaje))
            return
        self.root.after(0, lambda: self._pistas_exportadas(carpeta, len(elementos), duracion))

    def _error_exportando(self, error):
        self.boton_exportar.config(state="normal", text="Exportar mezcla como WAV...")
        self.boton_exportar_pistas.config(
            state="normal" if self.pistas else "disabled", text="Exportar cada pista por separado..."
        )
        messagebox.showerror("Error al exportar", error)

    def _exportado_listo(self, ruta, detalle, duracion):
        self.boton_exportar.config(state="normal", text="Exportar mezcla como WAV...")
        self._registrar_log(f"Mezcla exportada: {ruta}  {detalle}  tiempo={duracion:.3f}s")
        messagebox.showinfo("Exportado", f"Mezcla guardada en:\n{ruta}")

    def _pistas_exportadas(self, carpeta, cantidad, duracion):
        self.boton_exportar_pistas.config(state="normal", text="Exportar cada pista por separado...")
        self._registrar_log(f"{cantidad} pistas exportadas en {carpeta}  tiempo={duracion:.3f}s")
        messagebox.showinfo("Exportado", f"{cantidad} pistas guardadas en:\n{carpeta}")


def main():
    root = tk.Tk()
    AppEstudioAudio(root)
    root.mainloop()


if __name__ == "__main__":
    main()
