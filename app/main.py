"""
main.py — ventana principal (Tkinter), con tres pestañas:

  1. Separación con IA: cargar la canción o el video (archivo o enlace), elegir la
     calidad y ver el avance. Al terminar, se abre sola en Moldear.
  2. Moldear: editor de pistas en línea de tiempo (editor.py).
  3. DJ: modo DJ privado (por ahora solo la pestaña; viene después).
"""

import datetime
import os
import threading
import time
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import audio_io
import editor
import importador
import reproductor
import separador
import tema
from motor import ErrorMotor, MotorAudio
from widgets import BotonAnimado

NOMBRE_APP = "Waveforge"
RAIZ_PROYECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVO_LOG = os.path.join(RAIZ_PROYECTO, "registro.log")

COLOR_TEXTO_SUAVE = tema.TEXTO_SUAVE
COLOR_ERROR = tema.ERROR

# atajo de teclado -> acción del editor (solo con la pestaña Moldear activa)
ATAJOS = {
    "<space>": "espacio", "<Delete>": "suprimir", "<Control-z>": "deshacer", "<Control-y>": "rehacer",
    "<Control-Z>": "rehacer", "<plus>": "acercar", "<KP_Add>": "acercar", "<minus>": "alejar",
    "<KP_Subtract>": "alejar", "<Home>": "inicio", "<Control-a>": "todo", "<Escape>": "escape",
}


class AppEstudioAudio:
    def __init__(self, root):
        self.root = root
        self.root.title(NOMBRE_APP)
        self.root.geometry("1280x800")
        self.root.minsize(1100, 660)
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

        # Separación con IA
        self.var_calidad = tk.StringVar(value="rapida")  # clave de separador.CALIDADES
        self.var_publico = tk.BooleanVar(value=False)    # modo concierto: separar al público
        self._separando = False
        self._progreso = (0, 1)
        self._inicio_separacion = 0.0
        self._estado_separacion = ""
        self._separadas = False

        self.mezclador = reproductor.Mezclador()

        self._crear_menu()
        self.root.protocol("WM_DELETE_WINDOW", self._salir)

        self.pestanas = ttk.Notebook(self.root)
        self.pestanas.pack(fill="both", expand=True)
        self.editor = editor.EditorMoldear(self.pestanas, self.motor, self.mezclador, self._registrar_log,
                                           al_pedir_cancion=lambda: self.pestanas.select(self.tab_ia))
        self.tab_ia = ttk.Frame(self.pestanas)
        self.tab_dj = ttk.Frame(self.pestanas)
        # Primero se sube y separa la canción; después se moldea.
        self.pestanas.add(self.tab_ia, text="  ✦  1 · Separación con IA  ")
        self.pestanas.add(self.editor, text="  🎚  2 · Moldear  ")
        self.pestanas.add(self.tab_dj, text="  🎧  3 · DJ  ")

        self._construir_pestana_ia()
        self._construir_pestana_dj()
        for secuencia, accion in ATAJOS.items():
            self.root.bind(secuencia, lambda e, a=accion: self._atajo(e, a))

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
        self.mezclador.detener()
        self.root.destroy()

    def _registrar_log(self, mensaje):
        linea = f"{datetime.datetime.now().isoformat(timespec='seconds')}  {mensaje}\n"
        try:
            with open(ARCHIVO_LOG, "a", encoding="utf-8") as f:
                f.write(linea)
        except OSError:
            pass  # el log es informativo; un fallo aquí no debe tumbar la app

    def _atajo(self, evento, accion):
        """Los atajos solo actúan en Moldear, y no mientras se escribe en un campo de texto."""
        if self.pestanas.select() != str(self.editor):
            return
        if isinstance(evento.widget, (tk.Entry, ttk.Entry)) and not isinstance(evento.widget, ttk.Combobox):
            return
        self.editor.tecla(accion)
        return "break"

    def _crear_menu(self):
        barra_menu = tk.Menu(self.root)

        menu_archivo = tk.Menu(barra_menu, tearoff=0)
        menu_archivo.add_command(label="Cargar archivo...", command=self._elegir_archivo)
        menu_archivo.add_command(label="Pegar enlace...", command=self._importar_url)
        menu_archivo.add_separator()
        menu_archivo.add_command(label="Salir", command=self._salir)
        barra_menu.add_cascade(label="Archivo", menu=menu_archivo)

        menu_editar = tk.Menu(barra_menu, tearoff=0)
        menu_editar.add_command(label="Deshacer", accelerator="Ctrl+Z", command=lambda: self.editor.tecla("deshacer"))
        menu_editar.add_command(label="Rehacer", accelerator="Ctrl+Y", command=lambda: self.editor.tecla("rehacer"))
        menu_editar.add_separator()
        menu_editar.add_command(label="Seleccionar todo", accelerator="Ctrl+A", command=lambda: self.editor.tecla("todo"))
        barra_menu.add_cascade(label="Editar", menu=menu_editar)

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
            f"{NOMBRE_APP} — editor de audio con separación de pistas por IA.\n"
            "Motor de filtros en C++ (ctypes) + Python/Tkinter.",
        )

    # ---------- pestaña "Separación con IA" ----------

    def _construir_pestana_ia(self):
        cuerpo = ttk.Frame(self.tab_ia)
        cuerpo.pack(fill="both", expand=True, padx=28, pady=20)

        ttk.Label(cuerpo, text="Separación con IA", style="Titulo.TLabel").pack(anchor="w")
        ttk.Label(
            cuerpo,
            text="Carga una canción: la IA la separa en voz, coros e instrumentos y, al terminar, "
                 "se abre sola en «Moldear» para editarla.",
            foreground=COLOR_TEXTO_SUAVE,
        ).pack(anchor="w", pady=(2, 16))

        marco_cancion = ttk.LabelFrame(cuerpo, text="Canción")
        marco_cancion.pack(fill="x", pady=(0, 12))
        ttk.Label(
            marco_cancion,
            text="Acepta WAV, MP3, M4A, OGG, FLAC, video y similares (máximo 10 minutos), "
                 "o un enlace de YouTube u otro sitio.",
        ).pack(anchor="w", padx=12, pady=(10, 6))
        fila = ttk.Frame(marco_cancion)
        fila.pack(anchor="w", padx=12, pady=(0, 8))
        BotonAnimado(fila, "📂  Seleccionar archivo...", self._elegir_archivo, estilo="acento", alto=34).pack(side="left")
        BotonAnimado(fila, "🔗  Pegar enlace (YouTube u otro)...", self._importar_url, alto=34).pack(
            side="left", padx=(8, 0))
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
            text="Rápida: Demucs saca la voz (en una PC de 6 núcleos, menos de lo que dura la canción). "
                 "Alta: RoFormer saca una voz bastante más limpia, pero sin tarjeta NVIDIA tarda varias veces "
                 "lo que dura la canción (la primera vez descarga ~900 MB). Cambiarla con una canción cargada "
                 "la vuelve a separar; cada calidad queda guardada.",
            foreground=COLOR_TEXTO_SUAVE, wraplength=980, justify="left",
        ).pack(anchor="w", padx=12, pady=(6, 10))

        marco_tipo = ttk.LabelFrame(cuerpo, text="¿Es un concierto o una grabación en vivo?")
        marco_tipo.pack(fill="x", pady=(0, 12))
        ttk.Checkbutton(
            marco_tipo, text="🎤  Modo concierto: separar al público de la voz del cantante",
            variable=self.var_publico, command=self._cambiar_calidad,
        ).pack(anchor="w", padx=12, pady=(8, 0))
        ttk.Label(
            marco_tipo,
            text="Para videos de conciertos donde la gente canta, grita o aplaude. La gente queda en su propia "
                 "pista «Público» y la «Voz principal» queda solo con el cantante. Para dejar solo su voz: "
                 "en Moldear, botón S (solo) de «Voz principal», y exportar. Funciona mejor cuando el cantante "
                 "suena por encima del público; si todos cantan igual de fuerte, algo de público se cuela.",
            foreground=COLOR_TEXTO_SUAVE, wraplength=980, justify="left",
        ).pack(anchor="w", padx=12, pady=(4, 10))

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

    def _construir_pestana_dj(self):
        caja = ttk.Frame(self.tab_dj)
        caja.place(relx=0.5, rely=0.42, anchor="center")
        ttk.Label(caja, text="🎧", font=("TkDefaultFont", 40)).pack()
        ttk.Label(caja, text="Modo DJ", style="Titulo.TLabel").pack(pady=(6, 4))
        ttk.Label(
            caja,
            text="Próximamente: mezclar canciones como un DJ, con transiciones automáticas\n"
                 "según el tempo (BPM) y la tonalidad de cada una.",
            foreground=COLOR_TEXTO_SUAVE, justify="center",
        ).pack()

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
                    f"El audio dura {editor.formato_tiempo(info.duracion_segundos)}; "
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
        self._iniciar_separacion()

    # ---------- separación (arranca sola al cargar la canción) ----------

    def _cambiar_calidad(self):
        """Otra calidad o modo concierto: si hay una canción cargada, se vuelve a separar."""
        if self.info is not None:
            self._iniciar_separacion()

    def _iniciar_separacion(self):
        self._sesion += 1  # cancela la separación anterior, si seguía corriendo
        self._estado_separacion = ""
        self._separadas = False

        instrucciones = separador.falta_instalar()
        if instrucciones:
            self._separando = False
            self._estado_separacion = "La separación con IA no está instalada: se edita la canción entera."
            self._actualizar_progreso()
            self._abrir_en_moldear(None)
            messagebox.showinfo("Separación con IA", instrucciones)
            return

        calidad = self.var_calidad.get()
        self._separando = True
        self._progreso = (0, len(self.izquierdo))
        self._inicio_separacion = time.perf_counter()
        self.editor.mostrar_vacio("Separando con IA...\nLa canción se abre aquí sola cuando termine.\n"
                                  "El avance está en la pestaña «Separación con IA».")
        self._tick_progreso()

        threading.Thread(
            target=self._separar_en_hilo,
            args=(self._sesion, self.izquierdo, self.derecho, self.info.muestreo, calidad, self.var_publico.get()),
            daemon=True,
        ).start()

    def _separar_en_hilo(self, sesion, izquierdo, derecho, muestreo, calidad, publico):
        def avance(_pistas, hecho, total):
            self.root.after(0, lambda: self._on_avance_separacion(sesion, hecho, total))

        try:
            clave = separador.clave_audio(izquierdo, derecho, muestreo, calidad, publico)
            pistas = separador.cargar_de_cache(clave)
            desde_cache = pistas is not None
            if not desde_cache:
                pistas = separador.separar_por_partes(
                    izquierdo, derecho, muestreo, al_avanzar=avance,
                    cancelado=lambda: sesion != self._sesion, calidad=calidad, publico=publico,
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
        if not self._separando:
            return
        self._actualizar_progreso()
        self.root.after(500, self._tick_progreso)

    def _actualizar_progreso(self):
        if self._separando:
            hecho, total = self._progreso
            sr = self.info.muestreo
            transcurrido = editor.formato_tiempo(time.perf_counter() - self._inicio_separacion)
            if hecho:
                texto = (f"Separando con IA ({separador.CALIDADES[self.var_calidad.get()]})... "
                         f"{editor.formato_tiempo(hecho / sr)} de {editor.formato_tiempo(total / sr)} listo · "
                         f"{transcurrido}")
            else:
                espera = ("unos minutos (la primera vez descarga el modelo, ~900 MB)"
                          if self.var_calidad.get() == "alta" else "unos segundos")
                texto = f"Separando con IA... la primera parte estará lista en {espera} · {transcurrido}"
            valor = 1000 * hecho / max(total, 1)
        else:
            texto = self._estado_separacion or "Esperando una canción."
            valor = 1000 if self._separadas else 0
        self._etiqueta_progreso.config(text=texto)
        self._barra_progreso.config(value=valor)

    def _separacion_fallida(self, sesion, mensaje):
        if sesion != self._sesion:
            return
        self._separando = False
        self._estado_separacion = "No se pudo separar la canción: se edita entera, como una sola pista."
        self._actualizar_progreso()
        self._abrir_en_moldear(None)
        messagebox.showerror("No se pudo separar", mensaje)

    def _separacion_lista(self, sesion, pistas, desde_cache):
        if sesion != self._sesion:
            return  # mientras tanto se cargó otra canción o se cambió la calidad
        self._separando = False
        self._separadas = True
        segundos = time.perf_counter() - self._inicio_separacion
        if desde_cache:
            self._estado_separacion = "Pistas recuperadas de una separación anterior."
        else:
            self._estado_separacion = f"Separada en {editor.formato_tiempo(segundos)}."
            self._registrar_log(f"Pistas separadas en {segundos:.1f}s: " + ", ".join(p["etiqueta"] for p in pistas))
        self._actualizar_progreso()
        self._abrir_en_moldear(pistas)

    def _abrir_en_moldear(self, pistas):
        """Abre la canción en el editor: separada en pistas, o entera como una sola."""
        if not pistas:
            pistas = [{"id": "cancion", "etiqueta": "Canción", "color": tema.AZUL,
                       "izq": self.izquierdo, "der": self.derecho}]
        self.editor.fijar_original(self.izquierdo, self.derecho)
        self.editor.cargar(self.info, self._nombre_base, pistas, nota=self._estado_separacion)
        self.pestanas.select(self.editor)


def main():
    root = tk.Tk()
    AppEstudioAudio(root)
    root.mainloop()


if __name__ == "__main__":
    main()
