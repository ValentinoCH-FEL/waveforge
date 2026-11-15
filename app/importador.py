"""
importador.py — trae audio desde fuera del WAV nativo de la app:

  - convertir_a_wav(ruta): cualquier archivo de audio o video
    (mp3, m4a, ogg, flac, mp4...) a WAV PCM 16 bits / 44100 Hz / estéreo,
    usando ffmpeg por detrás.
  - descargar_audio(url): el audio de UN SOLO video (YouTube u otro
    sitio que soporte yt-dlp) ya convertido a ese mismo formato WAV,
    usando la librería yt-dlp (no el comando de consola).

Ambas funciones devuelven la ruta de un WAV temporal listo para pasarle
directo a audio_io.cargar_wav(...).

Uso personal de pruebas: lo que se descargue no debe redistribuirse,
por los derechos de autor del contenido original.
"""

import importlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from audio_io import MUESTREO_NATIVO


class ErrorImportacion(Exception):
    """Falta una herramienta externa, o la conversión/descarga falló."""
    pass


_PATRON_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _limpiar_texto_progreso(texto):
    """Quita los códigos de color ANSI que yt-dlp mete en el porcentaje
    (se ven como caracteres raros en la consola de Windows)."""
    return _PATRON_ANSI.sub("", texto or "").strip()


def _ffmpeg_disponible():
    return shutil.which("ffmpeg") is not None


_CARPETA_TEMPORAL = os.path.join(tempfile.gettempdir(), "estudio_audio_temp")


def _carpeta_temporal():
    """Una carpeta nueva por importación. Las de importaciones anteriores se borran:
    ese audio ya está cargado en memoria, así que los WAV temporales no se acumulan."""
    os.makedirs(_CARPETA_TEMPORAL, exist_ok=True)
    for nombre in os.listdir(_CARPETA_TEMPORAL):
        shutil.rmtree(os.path.join(_CARPETA_TEMPORAL, nombre), ignore_errors=True)
    return tempfile.mkdtemp(dir=_CARPETA_TEMPORAL)


def convertir_a_wav(ruta_origen):
    """Convierte cualquier archivo de audio/video a WAV PCM 16 bits,
    44100 Hz, estéreo, usando ffmpeg. Devuelve la ruta del WAV generado."""
    if not _ffmpeg_disponible():
        raise ErrorImportacion(
            "No se encontró ffmpeg en el sistema.\n"
            "Instálalo con: winget install ffmpeg\n"
            "y reinicia la terminal antes de volver a intentar."
        )

    nombre = os.path.splitext(os.path.basename(ruta_origen))[0]
    destino = os.path.join(_carpeta_temporal(), f"{nombre}_convertido.wav")

    comando = [
        "ffmpeg", "-y", "-i", ruta_origen,
        "-ar", str(MUESTREO_NATIVO), "-ac", "2", "-sample_fmt", "s16",
        destino,
    ]
    resultado = subprocess.run(comando, capture_output=True, text=True)
    if resultado.returncode != 0:
        detalle = resultado.stderr.strip().splitlines()[-1] if resultado.stderr else "error desconocido"
        raise ErrorImportacion(f"ffmpeg no pudo convertir el archivo:\n{detalle}")

    return destino


# ---------- yt-dlp siempre instalado y al día ----------
# YouTube cambia seguido y las versiones viejas de yt-dlp dejan de poder descargar.
# Por eso la app lo instala si falta y lo actualiza sola (una vez al día, en segundo plano).

_MARCA_REVISION = os.path.join(os.path.expanduser("~"), ".cache", "waveforge_yt_dlp_revisado")
_HORAS_ENTRE_REVISIONES = 24
_actualizacion_lista = threading.Event()
_actualizacion_lista.set()  # nada pendiente hasta que se lance una actualización


def _pip_instalar_yt_dlp():
    """Instala o actualiza yt-dlp en el mismo Python que corre la app. True si funcionó."""
    try:
        resultado = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q", "-U", "yt-dlp"],
            capture_output=True, text=True, timeout=180,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),  # sin ventana negra en Windows
        )
        return resultado.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def actualizar_yt_dlp_en_segundo_plano():
    """Llamar al abrir la app. Si pasó un día desde la última revisión, actualiza yt-dlp
    en un hilo aparte (si no hay internet, no pasa nada: se reintenta la próxima vez)."""
    try:
        if time.time() - os.path.getmtime(_MARCA_REVISION) < _HORAS_ENTRE_REVISIONES * 3600:
            return
    except OSError:
        pass  # nunca se revisó

    def trabajo():
        try:
            if _pip_instalar_yt_dlp():
                os.makedirs(os.path.dirname(_MARCA_REVISION), exist_ok=True)
                with open(_MARCA_REVISION, "w") as marca:
                    marca.write(time.strftime("%Y-%m-%d %H:%M"))
        finally:
            _actualizacion_lista.set()

    _actualizacion_lista.clear()
    threading.Thread(target=trabajo, daemon=True).start()


def _importar_yt_dlp(callback_progreso):
    # Si se está actualizando, se espera a que termine (importarlo a medio instalar fallaría).
    if not _actualizacion_lista.is_set() and callback_progreso:
        callback_progreso("actualizando yt-dlp...")
    _actualizacion_lista.wait(timeout=180)
    try:
        import yt_dlp
        return yt_dlp
    except ImportError:
        pass
    if callback_progreso:
        callback_progreso("instalando yt-dlp (solo esta vez)...")
    if _pip_instalar_yt_dlp():
        importlib.invalidate_caches()
        try:
            import yt_dlp
            return yt_dlp
        except ImportError:
            pass
    raise ErrorImportacion(
        "No se pudo instalar yt-dlp automáticamente (¿hay internet?).\n"
        "Instálalo a mano con: pip install -U yt-dlp"
    )


def descargar_audio(url, callback_progreso=None, duracion_maxima_s=None):
    """Descarga el audio de UN SOLO video (ignora parámetros de playlist
    en la URL) y lo deja como WAV, usando la librería yt-dlp. Requiere
    conexión a internet y ffmpeg instalado. Si el video dura más que
    duracion_maxima_s, se rechaza antes de descargar nada."""
    yt_dlp = _importar_yt_dlp(callback_progreso)

    if not _ffmpeg_disponible():
        raise ErrorImportacion(
            "yt-dlp necesita ffmpeg para convertir el audio a WAV.\n"
            "Instálalo con: winget install ffmpeg"
        )

    destino_base = os.path.join(_carpeta_temporal(), "descarga")

    def _hook(info):
        if callback_progreso is None:
            return
        estado = info.get("status")
        if estado == "downloading":
            callback_progreso(_limpiar_texto_progreso(info.get("_percent_str", "...")))
        elif estado == "finished":
            callback_progreso("procesando audio (ffmpeg)...")

    opciones = {
        "format": "bestaudio/best",
        "outtmpl": destino_base + ".%(ext)s",
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
        "postprocessor_args": ["-ar", str(MUESTREO_NATIVO), "-ac", "2"],
        "noplaylist": True,
        "no_color": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [_hook],
    }

    try:
        with yt_dlp.YoutubeDL(opciones) as ydl:
            info = ydl.extract_info(url, download=False)
            duracion = info.get("duration")
            if duracion_maxima_s and duracion and duracion > duracion_maxima_s:
                raise ErrorImportacion(
                    f"El video dura {int(duracion) // 60}:{int(duracion) % 60:02d}; "
                    f"el máximo permitido es {duracion_maxima_s // 60} minutos."
                )
            ydl.process_ie_result(info, download=True)
    except ErrorImportacion:
        raise
    except Exception as error:
        detalle = _limpiar_texto_progreso(str(error))
        # Lo más común cuando YouTube cambia algo: yt-dlp quedó viejo. Se actualiza para la próxima.
        if _pip_instalar_yt_dlp():
            detalle += "\n\nSe actualizó yt-dlp: cierra y vuelve a abrir la app, y prueba de nuevo."
        raise ErrorImportacion(f"No se pudo descargar el audio:\n{detalle}")

    ruta_wav = destino_base + ".wav"
    if not os.path.exists(ruta_wav):
        raise ErrorImportacion("La descarga terminó pero no se generó el WAV esperado.")

    return ruta_wav