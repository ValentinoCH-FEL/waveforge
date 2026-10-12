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

import os
import re
import shutil
import subprocess
import tempfile


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


def _carpeta_temporal():
    carpeta = os.path.join(tempfile.gettempdir(), "estudio_audio_temp")
    os.makedirs(carpeta, exist_ok=True)
    return carpeta


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
        "-ar", "44100", "-ac", "2", "-sample_fmt", "s16",
        destino,
    ]
    resultado = subprocess.run(comando, capture_output=True, text=True)
    if resultado.returncode != 0:
        detalle = resultado.stderr.strip().splitlines()[-1] if resultado.stderr else "error desconocido"
        raise ErrorImportacion(f"ffmpeg no pudo convertir el archivo:\n{detalle}")

    return destino


def descargar_audio(url, callback_progreso=None, duracion_maxima_s=None):
    """Descarga el audio de UN SOLO video (ignora parámetros de playlist
    en la URL) y lo deja como WAV, usando la librería yt-dlp. Requiere
    conexión a internet y ffmpeg instalado. Si el video dura más que
    duracion_maxima_s, se rechaza antes de descargar nada."""
    try:
        import yt_dlp
    except ImportError:
        raise ErrorImportacion(
            "Falta el paquete yt-dlp.\n"
            "Instálalo con: pip install yt-dlp"
        )

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
        "postprocessor_args": ["-ar", "44100", "-ac", "2"],
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
        raise ErrorImportacion(f"No se pudo descargar el audio:\n{_limpiar_texto_progreso(str(error))}")

    ruta_wav = destino_base + ".wav"
    if not os.path.exists(ruta_wav):
        raise ErrorImportacion("La descarga terminó pero no se generó el WAV esperado.")

    return ruta_wav