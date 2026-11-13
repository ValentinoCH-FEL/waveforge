"""
main.py — ventana principal (Tkinter), con dos pestañas:

  - Separación con IA: cargar la canción (archivo o enlace), elegir la calidad
    de la separación y ver su avance. Al terminar pasa sola a Estudio.
  - Estudio: la canción ya separada. Pistas (volumen, silenciar, solo), un efecto
    por pista, las ondas de la canción completa, una barra de reproducción que
    avanza y se puede arrastrar, y exportar la mezcla o cada pista.
    Mientras la IA separa, Estudio espera: se edita la canción completa ya separada.
"""

import datetime
import os
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
import procesamiento
import reproductor
import separador
import tema
from motor import ErrorMotor, MotorAudio

NOMBRE_APP = "Waveforge"
RAIZ_PROYECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVO_LOG = os.path.join(RAIZ_PROYECTO, "registro.log")

MILISEGUNDOS_DEBOUNCE = 180   # espera tras mover un control antes de recalcular
MILISEGUNDOS_BARRA = 50       # cada cuánto avanza la barra de reproducción

COLOR_TEXTO_SUAVE = tema.TEXTO_SUAVE
COLOR_ERROR = tema.ERROR


def formato_tiempo(segundos):
    segundos = int(round(segundos))
    return f"{segundos // 60}:{segundos % 60:02d}"


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

        # Canción cargada
        self.info = None
        self.izquierdo = None
        self.derecho = None
        self.historial = []
        self._nombre_base = "audio"
        self._sesion = 0  # sube con cada canción o separación nueva: descarta resultados viejos

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

        # Separación con IA
        self.var_calidad = tk.StringVar(value="rapida")  # clave de separador.CALIDADES
        self._separando = False
        self._progreso = (0, 1)          # (muestras ya separadas, total)
        self._inicio_separacion = 0.0
        self._estado_separacion = ""     # mensaje al terminar o fallar la separación

        # Estudio
        self.var_parametro = None
        self._etiqueta_valor = None
        self._info_parametro = None
        self._debounce_id = None
        self._suspender_auto_reproduccion = False
        self._mezclador = reproductor.Mezclador()
        self._suena_original = False     # True: el mezclador tiene el audio original, no la mezcla
        self._audio_mezcla = None        # (pistas procesadas, ganancias) de la última vista previa
        self._ultimo_procesado = None    # (clave, pistas con filtro): evita recalcular si solo cambió el volumen
        self._grafica = None             # existen solo mientras el Estudio está armado
        self._barra = None
        self._etiqueta_espera = None

        self._crear_menu()
        self.root.protocol("WM_DELETE_WINDOW", self._salir)

        self.pestanas = ttk.Notebook(self.root)
        self.pestanas.pack(fill="both", expand=True)
        self.tab_ia = ttk.Frame(self.pestanas)
        self.tab_estudio = ttk.Frame(self.pestanas)
        self.pestanas.add(self.tab_ia, text="  Separación con IA  ")
        self.pestanas.add(self.tab_estudio, text="  Estudio  ")

        self._construir_pestana_ia()
        self._mostrar_estudio_en_espera("Carga una canción en la pestaña «Separación con IA».")
        self._tick_reproduccion()

        # Mientras el usuario elige o descarga la canción, se cargan los modelos de IA.
        threading.Thread(target=separador.precargar_modelos, daemon=True).start()
        importador.actualizar_yt_dlp_en_segundo_plano()  # YouTube cambia seguido

    # ---------- utilidades comunes ----------

    def _mostrar_excepcion_tk(self, tipo, valor, tb):
        """Tkinter se traga las excepciones de los callbacks y deja la ventana a medias.
        Esto las muestra en pantalla y las guarda en el registro."""
        detalle = "".join(traceback.format_exception(tipo, valor, tb))
        self._registrar_log("EXCEPCION EN LA INTERFAZ:\n" + detalle)
        messagebox.showerror("Error en la interfaz", detalle[-1500:])

    def _salir(self):
        """Cierra la salida de audio antes de destruir la ventana."""
        self._mezclador.detener()
        self.root.destroy()

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
        menu_archivo.add_command(label="Pegar enlace...", command=self._importar_url)
        menu_archivo.add_separator()
        menu_archivo.add_command(label="Salir", command=self._salir)
        barra_menu.add_cascade(label="Archivo", menu=menu_archivo)

        menu_herramientas = tk.Menu(barra_menu, tearoff=0)
        menu_herramientas.add_command(label="Ver registro de la sesión...", command=self._ver_registro)
        barra_menu.add_cascade(label="Herramientas", menu=menu_herramientas)

        menu_ayuda = tk.Menu(barra_menu, tearoff=0)
        menu_ayuda.add_command(label="Acerca de", command=self._acerca_de)
        barra_menu.add_cascade(label="Ayuda", menu=menu_ayuda)

        self.root.config(menu=barra_menu)

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
            f"{NOMBRE_APP} — estudio de audio con separación de pistas por IA.\n"
            "Motor de filtros en C++ (ctypes) + Python/Tkinter.",
        )

    # ---------- pestaña "Separación con IA" ----------

    def _construir_pestana_ia(self):
        cuerpo = ttk.Frame(self.tab_ia)
        cuerpo.pack(fill="both", expand=True, padx=28, pady=20)

        ttk.Label(cuerpo, text=NOMBRE_APP, style="Titulo.TLabel").pack(anchor="w")
        ttk.Label(
            cuerpo,
            text="Carga una canción: la IA la separa en voz, coros e instrumentos y, al terminar, "
                 "pasa sola a la pestaña Estudio.",
            foreground=COLOR_TEXTO_SUAVE,
        ).pack(anchor="w", pady=(2, 16))

        marco_cancion = ttk.LabelFrame(cuerpo, text="Canción")
        marco_cancion.pack(fill="x", pady=(0, 12))
        ttk.Label(
            marco_cancion,
            text="Acepta WAV, MP3, M4A, OGG, FLAC, video y similares (máximo 10 minutos), "
                 "o un enlace de YouTube u otro sitio.",
        ).pack(anchor="w", padx=12, pady=(10, 4))
        fila = ttk.Frame(marco_cancion)
        fila.pack(anchor="w", padx=12, pady=(0, 8))
        ttk.Button(fila, text="Seleccionar archivo...", command=self._elegir_archivo).pack(side="left")
        ttk.Button(fila, text="Pegar enlace (YouTube u otro)...", command=self._importar_url).pack(
            side="left", padx=(8, 0)
        )
        self.etiqueta_info = ttk.Label(marco_cancion, text="Ninguna canción cargada todavía.",
                                       foreground=COLOR_TEXTO_SUAVE)
        self.etiqueta_info.pack(anchor="w", padx=12, pady=(0, 10))

        marco_calidad = ttk.LabelFrame(cuerpo, text="Calidad de la separación")
        marco_calidad.pack(fill="x", pady=(0, 12))
        for clave, etiqueta in separador.CALIDADES.items():
            ttk.Radiobutton(
                marco_calidad, text=etiqueta, value=clave, variable=self.var_calidad,
                command=self._cambiar_calidad,
            ).pack(anchor="w", padx=12, pady=(8, 0))
        ttk.Label(
            marco_calidad,
            text="Cambiarla con una canción cargada la vuelve a separar. La alta saca la voz más limpia, "
                 "pero sin tarjeta NVIDIA tarda varias veces lo que dura la canción "
                 "(la primera vez descarga ~900 MB).",
            foreground=COLOR_TEXTO_SUAVE, wraplength=900, justify="left",
        ).pack(anchor="w", padx=12, pady=(6, 10))

        marco_avance = ttk.LabelFrame(cuerpo, text="Avance")
        marco_avance.pack(fill="x", pady=(0, 12))
        self._etiqueta_progreso = ttk.Label(marco_avance, text="Esperando una canción.",
                                            foreground=COLOR_TEXTO_SUAVE)
        self._etiqueta_progreso.pack(anchor="w", padx=12, pady=(10, 4))
        self._barra_progreso = ttk.Progressbar(marco_avance, mode="determinate", maximum=1000)
        self._barra_progreso.pack(fill="x", padx=12, pady=(0, 12))

        self._marco_historial = ttk.LabelFrame(cuerpo, text="Canciones de esta sesión")
        self._marco_historial.pack(fill="both", expand=True)
        self._actualizar_historial()

    def _actualizar_historial(self):
        for widget in self._marco_historial.winfo_children():
            widget.destroy()
        if not self.historial:
            ttk.Label(self._marco_historial, text="Todavía ninguna.", foreground=COLOR_TEXTO_SUAVE).pack(
                anchor="w", padx=12, pady=8
            )
        for ruta in self.historial[-8:]:
            ttk.Label(self._marco_historial, text=os.path.basename(ruta)).pack(anchor="w", padx=12, pady=2)

    def _mostrar_info(self, texto, color=COLOR_TEXTO_SUAVE):
        self.etiqueta_info.config(text=texto, foreground=color)

    # ---------- cargar la canción ----------

    def _elegir_archivo(self):
        ruta = filedialog.askopenfilename(
            filetypes=[
                ("Audio y video", "*.wav *.mp3 *.m4a *.ogg *.flac *.aac *.mp4 *.mov *.mkv"),
                ("Todos los archivos", "*.*"),
            ]
        )
        if not ruta:
            return
        self.pestanas.select(self.tab_ia)

        if audio_io.es_wav_nativo(ruta):
            self._cargar_ruta_wav(ruta, ruta_original=ruta)
            return

        self._mostrar_info("Convirtiendo a WAV...")
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
        self._mostrar_info("No se pudo importar la canción.", COLOR_ERROR)
        messagebox.showerror("Error al importar", str(error))

    def _importar_url(self):
        url = simpledialog.askstring(
            "Pegar enlace",
            "Enlace de YouTube u otro sitio soportado por yt-dlp:",
            parent=self.root,
        )
        if not url:
            return
        self.pestanas.select(self.tab_ia)
        self._mostrar_info("Descargando audio...")
        self.root.update_idletasks()
        threading.Thread(target=self._descargar_en_hilo, args=(url,), daemon=True).start()

    def _descargar_en_hilo(self, url):
        def progreso(porcentaje):
            self.root.after(0, lambda: self._mostrar_info(f"Descargando... {porcentaje}"))

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
                    f"El audio dura {formato_tiempo(info.duracion_segundos)}; "
                    f"el máximo permitido es {separador.DURACION_MAXIMA_S // 60} minutos."
                )
        except audio_io.ErrorFormatoAudio as error:
            messagebox.showerror("Archivo no válido", str(error))
            self._mostrar_info("Ninguna canción cargada todavía.")
            return
        self.info, self.izquierdo, self.derecho = info, izquierdo, derecho

        if ruta_original not in self.historial:
            self.historial.append(ruta_original)
        self._actualizar_historial()
        self._registrar_log(f"Archivo cargado: {ruta_original}")
        self._nombre_base = (
            os.path.splitext(os.path.basename(ruta_original))[0] if os.path.exists(ruta_original) else "audio"
        )
        self._mostrar_info(f"{self._nombre_base}  ·  {info.resumen()}")

        # Canción nueva: se empieza de cero (sin pistas ni efectos, reproducción al inicio).
        self._mezclador.detener()
        self._filtro_por_pista = {}
        self._valores_parametros = {}
        self._volumen, self._silencio, self._solo = {}, {}, {}
        self._iniciar_separacion()

    # ---------- separación (arranca sola al cargar la canción) ----------

    def _cambiar_calidad(self):
        """Otra calidad elegida: si hay una canción cargada, se vuelve a separar con ella."""
        if self.info is None:
            return  # se usará con la próxima canción
        self._mezclador.detener()
        self._iniciar_separacion()

    def _iniciar_separacion(self):
        self._sesion += 1  # cancela la separación anterior, si seguía corriendo
        self.pistas = None
        self._pista_sel = "mezcla"
        self.var_pista_sel.set("mezcla")
        self._asegurar_controles("mezcla")
        self._estado_separacion = ""

        instrucciones = separador.falta_instalar()
        if instrucciones:
            self._separando = False
            self._estado_separacion = "La separación con IA no está instalada: se edita la canción entera."
            self._actualizar_progreso()
            self._construir_estudio()
            self.pestanas.select(self.tab_estudio)
            messagebox.showinfo("Separación con IA", instrucciones)
            return

        calidad = self.var_calidad.get()
        self._separando = True
        self._progreso = (0, len(self.izquierdo))
        self._inicio_separacion = time.perf_counter()
        self._mostrar_estudio_en_espera(
            "Separando con IA... el Estudio se abre solo cuando termine.\n"
            "El avance está en la pestaña «Separación con IA»."
        )
        self._tick_progreso()

        threading.Thread(
            target=self._separar_en_hilo,
            args=(self._sesion, self.izquierdo, self.derecho, self.info.muestreo, calidad),
            daemon=True,
        ).start()

    def _separar_en_hilo(self, sesion, izquierdo, derecho, muestreo, calidad):
        def avance(_pistas, hecho, total):
            self.root.after(0, lambda: self._on_avance_separacion(sesion, hecho, total))

        try:
            clave = separador.clave_audio(izquierdo, derecho, muestreo, calidad)
            pistas = separador.cargar_de_cache(clave)
            desde_cache = pistas is not None
            if not desde_cache:
                pistas = separador.separar_por_partes(
                    izquierdo, derecho, muestreo, al_avanzar=avance,
                    cancelado=lambda: sesion != self._sesion, calidad=calidad,
                )
                if pistas is None:
                    return  # cancelada: otra canción u otra calidad
                separador.guardar_en_cache(clave, pistas)
        except Exception as error:  # cualquier fallo del modelo: avisar, no cerrar la app
            mensaje = str(error) if isinstance(error, separador.ErrorSeparacion) else f"Error inesperado:\n{error}"
            self.root.after(0, lambda: self._separacion_fallida(sesion, mensaje))
            return
        self.root.after(0, lambda: self._separacion_lista(sesion, pistas, desde_cache))

    def _on_avance_separacion(self, sesion, hecho, total):
        if sesion == self._sesion and self._separando:
            self._progreso = (hecho, total)
            self._actualizar_progreso()

    def _tick_progreso(self):
        """Actualiza el reloj de la separación cada medio segundo."""
        if not self._separando:
            return
        self._actualizar_progreso()
        self.root.after(500, self._tick_progreso)

    def _actualizar_progreso(self):
        if self._separando:
            hecho, total = self._progreso
            sr = self.info.muestreo
            transcurrido = formato_tiempo(time.perf_counter() - self._inicio_separacion)
            if hecho:
                texto = (f"Separando con IA ({separador.CALIDADES[self.var_calidad.get()]})... "
                         f"{formato_tiempo(hecho / sr)} de {formato_tiempo(total / sr)} listo · {transcurrido}")
            else:
                espera = ("unos minutos (la primera vez descarga el modelo, ~900 MB)"
                          if self.var_calidad.get() == "alta" else "unos segundos")
                texto = f"Separando con IA... la primera parte estará lista en {espera} · {transcurrido}"
            valor = 1000 * hecho / max(total, 1)
        else:
            texto = self._estado_separacion or "Esperando una canción."
            valor = 1000 if self.pistas else 0
        self._etiqueta_progreso.config(text=texto)
        self._barra_progreso.config(value=valor)

    def _separacion_fallida(self, sesion, mensaje):
        if sesion != self._sesion:
            return
        self._separando = False
        self._estado_separacion = "No se pudo separar la canción: se edita entera, como una sola pista."
        self._actualizar_progreso()
        self._construir_estudio()
        messagebox.showerror("No se pudo separar", mensaje)

    def _separacion_lista(self, sesion, pistas, desde_cache):
        if sesion != self._sesion:
            return  # mientras tanto se cargó otra canción o se cambió la calidad
        self._separando = False
        segundos = time.perf_counter() - self._inicio_separacion
        nombres = ", ".join(p["etiqueta"] for p in pistas)
        if desde_cache:
            self._estado_separacion = "Pistas recuperadas de una separación anterior."
        else:
            self._estado_separacion = f"Separada en {formato_tiempo(segundos)}."
            self._registrar_log(f"Pistas separadas en {segundos:.1f}s: {nombres}")

        self.pistas = pistas or None
        for pista in pistas:
            self._asegurar_controles(pista["id"])
        if pistas:
            self._pista_sel = pistas[0]["id"]
            self.var_pista_sel.set(self._pista_sel)
        self._actualizar_progreso()
        self._construir_estudio()
        self.pestanas.select(self.tab_estudio)

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

    def _duracion_total(self):
        return len(self.izquierdo) / float(self.info.muestreo)

    # ---------- pestaña "Estudio" ----------

    def _limpiar_estudio(self):
        self._grafica = None
        self._barra = None
        self._etiqueta_espera = None
        self._ultimo_procesado = None
        self._audio_mezcla = None
        for widget in self.tab_estudio.winfo_children():
            widget.destroy()

    def _mostrar_estudio_en_espera(self, texto):
        self._limpiar_estudio()
        self._etiqueta_espera = ttk.Label(
            self.tab_estudio, text=texto, foreground=COLOR_TEXTO_SUAVE, justify="center", anchor="center",
            font=("TkDefaultFont", 11),
        )
        self._etiqueta_espera.pack(expand=True)

    def _construir_estudio(self):
        self._limpiar_estudio()
        self._suena_original = False

        barra = ttk.Frame(self.tab_estudio)
        barra.pack(fill="x", padx=18, pady=(12, 6))
        ttk.Label(barra, text=self._nombre_base, style="Titulo.TLabel").pack(side="left")
        ttk.Label(barra, text=f"  ·  {self.info.resumen()}", foreground=COLOR_TEXTO_SUAVE).pack(side="left")
        ttk.Button(barra, text="Otra canción...", command=lambda: self.pestanas.select(self.tab_ia)).pack(
            side="right"
        )
        ttk.Separator(self.tab_estudio).pack(fill="x")

        pie = ttk.Frame(self.tab_estudio)
        pie.pack(fill="x", padx=18, pady=12, side="bottom")
        self.boton_exportar = ttk.Button(
            pie, text="Exportar mezcla como WAV...", style="Acento.TButton", command=self._exportar_mezcla
        )
        self.boton_exportar.pack(side="right")
        self.boton_exportar_pistas = ttk.Button(
            pie, text="Exportar cada pista por separado...", command=self._exportar_pistas
        )
        self.boton_exportar_pistas.pack(side="right", padx=(0, 10))
        self.boton_exportar.config(state="normal" if self.motor is not None else "disabled")
        self.boton_exportar_pistas.config(state="normal" if self.pistas and self.motor else "disabled")

        cuerpo = ttk.Frame(self.tab_estudio)
        cuerpo.pack(fill="both", expand=True, padx=18, pady=10)

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

        # ----- columna derecha: ondas, barra de reproducción y controles -----
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

        fila = ttk.Frame(columna_der)
        fila.pack(side="bottom", fill="x", pady=(6, 0))
        ttk.Button(fila, text="▶ Reproducir", command=self._reproducir_mezcla).pack(side="left")
        ttk.Button(fila, text="▶ Original", command=self._reproducir_original).pack(side="left", padx=(8, 0))
        ttk.Button(fila, text="❚❚ Pausa", command=self._mezclador.pausar).pack(side="left", padx=(8, 0))
        ttk.Button(fila, text="■ Detener", command=self._mezclador.detener).pack(side="left", padx=(8, 0))
        self.var_autoreproducir = tk.BooleanVar(value=reproductor.disponible())
        check_auto = ttk.Checkbutton(fila, text="Escuchar al mover un control", variable=self.var_autoreproducir)
        check_auto.pack(side="left", padx=(16, 0))
        if not reproductor.disponible():
            check_auto.config(state="disabled")
            self.var_autoreproducir.set(False)
        self._etiqueta_tiempo = ttk.Label(fila, text="", foreground=COLOR_TEXTO_SUAVE, font=("TkFixedFont", 10))
        self._etiqueta_tiempo.pack(side="right")

        self._barra = graficas.BarraReproduccion(columna_der, al_buscar=self._ir_a_fraccion)
        self._barra.widget.pack(side="bottom", fill="x", pady=(4, 0))

        self._grafica = graficas.GraficaPistas(columna_der)
        self._grafica.widget.pack(side="top", fill="both", expand=True)

        self._construir_panel_pistas()
        self._construir_lista_filtros()
        self._refrescar_vista_sin_sonar()  # no sonar nada al abrir el Estudio

    # ---------- panel de pistas ----------

    def _construir_panel_pistas(self):
        for widget in self._marco_pistas.winfo_children():
            widget.destroy()
        self._etiquetas_volumen = {}
        if self._estado_separacion:
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
        etiqueta = ttk.Label(abajo, text=f"{round(self._volumen[id_p].get())}%", width=5, anchor="e",
                             foreground=COLOR_TEXTO_SUAVE)
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
        if not self._suena_original and self._mezclador.cantidad_pistas == len(elementos):
            self._mezclador.cambiar_ganancias([e["ganancia"] for e in elementos])
        self._programar_vista_previa(espera_ms=400)

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
        # Limpia el lugar del parámetro de todos los filtros (solo el elegido muestra el suyo).
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
        self._etiqueta_valor = ttk.Label(marco, text=self._texto_parametro(p, valor), font=("TkDefaultFont", 9))
        self._etiqueta_valor.pack(anchor="w")
        ttk.Scale(
            marco, from_=p["min"], to=p["max"], variable=self.var_parametro, orient="horizontal",
            command=self._on_param_change,
        ).pack(fill="x")

    def _on_param_change(self, valor):
        id_filtro = self._filtro_por_pista.get(self._pista_sel, "ninguno")
        self._valores_parametros[(self._pista_sel, id_filtro)] = float(valor)
        if self._etiqueta_valor is not None and self._info_parametro is not None:
            self._etiqueta_valor.config(text=self._texto_parametro(self._info_parametro, float(valor)))
        self._programar_vista_previa()

    # ---------- vista previa (siempre la canción completa) ----------

    def _programar_vista_previa(self, espera_ms=MILISEGUNDOS_DEBOUNCE):
        if self._debounce_id is not None:
            self.root.after_cancel(self._debounce_id)
        self._debounce_id = self.root.after(espera_ms, self._generar_vista_previa)

    def _refrescar_vista_sin_sonar(self):
        """Recalcula y redibuja la vista previa sin arrancar la reproducción por su cuenta."""
        self._suspender_auto_reproduccion = True
        self._generar_vista_previa(forzar=True)
        self._suspender_auto_reproduccion = False

    def _generar_vista_previa(self, forzar=False):
        """forzar=True recalcula los filtros aunque nada haya cambiado."""
        self._debounce_id = None
        if self.motor is None or self.info is None or self._grafica is None:
            return

        n = len(self.izquierdo)
        elementos = self._snapshot()
        # Si solo cambió un volumen/M/S, los filtros dan lo mismo que antes: se reutiliza.
        clave = tuple((e["pista"]["id"], id(e["pista"]["izq"]), id(e["info"]), e["valor"]) for e in elementos)
        inicio = time.perf_counter()
        if not forzar and self._ultimo_procesado is not None and self._ultimo_procesado[0] == clave:
            procesadas = self._ultimo_procesado[1]
        else:
            try:
                procesadas = self._procesar_snapshot(elementos, 0, n)
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
                carriles.append((e["pista"]["etiqueta"].upper(), e["pista"]["color"], izq, e["ganancia"] == 0.0, escala))
            escala_comun = True
        else:
            carriles = [
                ("ORIGINAL", tema.AZUL, self.izquierdo, False, 1.0),
                ("PROCESADO", tema.ROJO, procesadas[0][0], False, 1.0),
            ]
            escala_comun = False

        self._grafica.dibujar(carriles, sr, escala_comun=escala_comun)
        self.etiqueta_estado_previa.config(
            text=f"Vista previa actualizada ({duracion * 1000:.0f} ms) · canción completa "
                 f"({formato_tiempo(self._duracion_total())})",
            foreground=COLOR_TEXTO_SUAVE,
        )
        self._configurar_audio(procesadas, [e["ganancia"] for e in elementos])

    # ---------- reproducción ----------

    def _configurar_audio(self, procesadas, ganancias):
        """Deja lista la mezcla para escucharla. Si ya está sonando, el cambio se oye sin cortar
        y sin perder la posición."""
        self._audio_mezcla = (procesadas, ganancias)
        if self._suena_original:
            return  # se está escuchando el original: la mezcla se carga al volver a ella
        self._mezclador.configurar(self.info.muestreo, procesadas, ganancias)
        if self.var_autoreproducir.get() and not self._suspender_auto_reproduccion and not self._mezclador.sonando:
            self._iniciar_audio()

    def _iniciar_audio(self):
        try:
            self._mezclador.iniciar()
        except reproductor.ErrorReproduccion as error:
            messagebox.showerror("No se pudo reproducir", str(error))

    def _reproducir_mezcla(self):
        if self._audio_mezcla is None:
            return
        procesadas, ganancias = self._audio_mezcla
        self._suena_original = False
        self._mezclador.configurar(self.info.muestreo, procesadas, ganancias)  # sigue en el mismo segundo
        self._iniciar_audio()

    def _reproducir_original(self):
        if self.info is None:
            return
        self._suena_original = True
        self._mezclador.configurar(self.info.muestreo, [(self.izquierdo, self.derecho)], [1.0])
        self._iniciar_audio()

    def _ir_a_fraccion(self, fraccion):
        """Click o arrastre en la barra de reproducción: saltar a esa parte de la canción."""
        self._mezclador.ir_a(fraccion * self._mezclador.largo)
        self._actualizar_barra()

    def _tick_reproduccion(self):
        """Mueve la barra de reproducción mientras suena (corre siempre, es muy liviano)."""
        if self._mezclador.termino and self._mezclador.sonando:
            self._mezclador.pausar()  # llegó al final: se para ahí; Reproducir vuelve a empezar
        self._actualizar_barra()
        self.root.after(MILISEGUNDOS_BARRA, self._tick_reproduccion)

    def _actualizar_barra(self):
        if self._barra is None or self.info is None:
            return
        largo = self._mezclador.largo or len(self.izquierdo)
        fraccion = self._mezclador.posicion / max(largo, 1)
        self._barra.mostrar(fraccion)
        sr = self.info.muestreo
        que = "original" if self._suena_original else "mezcla"
        estado = f"▶ {que}" if self._mezclador.sonando else "en pausa"
        self._etiqueta_tiempo.config(
            text=f"{estado}   {formato_tiempo(self._mezclador.posicion / sr)} / {formato_tiempo(largo / sr)}"
        )

    # ---------- exportar ----------

    def _exportar_mezcla(self):
        if self.motor is None:
            messagebox.showwarning("Motor no disponible", "Compila la librería nativa primero.")
            return
        ruta = filedialog.asksaveasfilename(
            defaultextension=".wav", initialfile=f"{self._nombre_base}_mezcla.wav", filetypes=[("Audio WAV", "*.wav")]
        )
        if not ruta:
            return
        elementos = self._snapshot()
        mono_de_origen = self.derecho is None and not self.pistas
        # El hilo trabaja con esta foto del audio: si mientras tanto se carga otra canción, no se mezclan.
        datos = (len(self.izquierdo), self.info.muestreo)

        self.boton_exportar.config(state="disabled", text="Procesando la canción...")
        self.root.update_idletasks()
        threading.Thread(
            target=self._exportar_mezcla_en_hilo, args=(ruta, elementos, datos, mono_de_origen), daemon=True,
        ).start()

    def _exportar_mezcla_en_hilo(self, ruta, elementos, datos, mono_de_origen):
        n, muestreo = datos
        try:
            inicio = time.perf_counter()
            procesadas = self._procesar_snapshot(elementos, 0, n)
            izq, der = procesamiento.mezclar(procesadas, [e["ganancia"] for e in elementos])
            audio_io.guardar_wav(ruta, muestreo, izq, None if mono_de_origen else der)
            duracion = time.perf_counter() - inicio
        except Exception as error:
            mensaje = str(error)
            self.root.after(0, lambda: self._error_exportando(mensaje))
            return
        self.root.after(0, lambda: self._exportado_listo(ruta, duracion))

    def _exportar_pistas(self):
        if not self.pistas or self.motor is None:
            return
        carpeta = filedialog.askdirectory(title="Carpeta donde guardar cada pista")
        if not carpeta:
            return
        elementos = self._snapshot()
        datos = (len(self.izquierdo), self.info.muestreo, self._nombre_base)
        self.boton_exportar_pistas.config(state="disabled", text="Guardando pistas...")
        self.root.update_idletasks()
        threading.Thread(target=self._exportar_pistas_en_hilo, args=(carpeta, elementos, datos), daemon=True).start()

    def _exportar_pistas_en_hilo(self, carpeta, elementos, datos):
        n, muestreo, nombre_base = datos
        try:
            inicio = time.perf_counter()
            for e in elementos:
                izq, der = self._procesar_snapshot([e], 0, n)[0]
                volumen = np.float32(e["vol"])  # cada pista sale con su filtro y su volumen (sin silenciar/solo)
                ruta = os.path.join(carpeta, f"{nombre_base}_{e['pista']['id']}.wav")
                audio_io.guardar_wav(ruta, muestreo, izq * volumen, der * volumen)
            duracion = time.perf_counter() - inicio
        except Exception as error:
            mensaje = str(error)
            self.root.after(0, lambda: self._error_exportando(mensaje))
            return
        self.root.after(0, lambda: self._pistas_exportadas(carpeta, len(elementos), duracion))

    def _restaurar_botones_exportar(self):
        for boton, texto in ((getattr(self, "boton_exportar", None), "Exportar mezcla como WAV..."),
                             (getattr(self, "boton_exportar_pistas", None), "Exportar cada pista por separado...")):
            if boton is not None and boton.winfo_exists():
                boton.config(state="normal", text=texto)
        if getattr(self, "boton_exportar_pistas", None) is not None and self.boton_exportar_pistas.winfo_exists():
            self.boton_exportar_pistas.config(state="normal" if self.pistas else "disabled")

    def _error_exportando(self, error):
        self._restaurar_botones_exportar()
        messagebox.showerror("Error al exportar", error)

    def _exportado_listo(self, ruta, duracion):
        self._restaurar_botones_exportar()
        self._registrar_log(f"Mezcla exportada: {ruta}  tiempo={duracion:.3f}s")
        messagebox.showinfo("Exportado", f"Mezcla guardada en:\n{ruta}")

    def _pistas_exportadas(self, carpeta, cantidad, duracion):
        self._restaurar_botones_exportar()
        self._registrar_log(f"{cantidad} pistas exportadas en {carpeta}  tiempo={duracion:.3f}s")
        messagebox.showinfo("Exportado", f"{cantidad} pistas guardadas en:\n{carpeta}")


def main():
    root = tk.Tk()
    AppEstudioAudio(root)
    root.mainloop()


if __name__ == "__main__":
    main()
