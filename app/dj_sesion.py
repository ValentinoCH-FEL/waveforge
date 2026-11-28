"""
dj_sesion.py — la sesión del DJ: suena sin parar y prepara cada mezcla mientras tanto. Sin Tkinter.

  - Biblioteca: las canciones de una carpeta o USB, analizadas de a poco en segundo plano
    (BPM y tonalidad; cada una se analiza una sola vez, queda guardado).
  - Mientras suena A, un hilo elige la siguiente (tempo parecido, tonalidad compatible, algo
    de azar), la carga, y arma la transición con dj_motor. En memoria hay como mucho dos
    canciones: cuando A termina de sonar se descarta.
  - Cada transición queda registrada con tu voto (👍/👎) en ~/.waveforge/dj_transiciones.jsonl.
    Las recetas con más 👍 se eligen más seguido, y ese archivo es la base para entrenar
    después un modelo con tu gusto.
"""

import json
import os
import threading
import time

import numpy as np

import dj_analisis
import dj_equipo
import dj_juez
import dj_letra
import dj_motor
from dj_analisis import SR

try:
    import sounddevice as sd
except Exception:  # sin salida de audio: la interfaz avisa
    sd = None

RUTA_VOTOS = os.path.join(dj_analisis.CARPETA_DATOS, "dj_transiciones.jsonl")


def pesos_recetas():
    """Peso de cada receta según tus votos: (👍 + 1) / (votos + 2). Sin votos, todas pesan igual."""
    cuenta = {}
    try:
        with open(RUTA_VOTOS, encoding="utf-8") as f:
            for linea in f:
                try:
                    r = json.loads(linea)
                except ValueError:
                    continue
                buenas, total = cuenta.get(r.get("receta"), (0, 0))
                cuenta[r.get("receta")] = (buenas + (1 if r.get("voto", 0) > 0 else 0), total + 1)
    except OSError:
        pass
    return {receta: (buenas + 1) / (total + 2) for receta, (buenas, total) in cuenta.items()}


def salidas_de_audio():
    """[(id, nombre)] de las salidas de audio estéreo del equipo (sin repetirlas por cada driver)."""
    try:
        api = sd.query_hostapis(sd.default.hostapi)
        return [(i, sd.query_devices(i)["name"]) for i in api["devices"]
                if sd.query_devices(i)["max_output_channels"] >= 2
                and "sound mapper" not in sd.query_devices(i)["name"].lower()]  # virtual de Windows
    except Exception:  # noqa: BLE001
        return []


class SesionDJ:
    def __init__(self, al_evento=None):
        """al_evento(tipo, texto) se llama desde hilos de fondo: la interfaz debe pasarlo a su hilo."""
        self._al_evento = al_evento or (lambda tipo, texto: None)
        self.biblioteca = []          # [{ruta, titulo, bpm, tono, camelot, error}]
        self.carpeta = None
        self._rng = np.random.default_rng()
        self._candado = threading.Lock()
        self._tramos = []             # lo que va a sonar, en orden: canción, mezcla, canción...
        self._stream = None
        self._parar = threading.Event()
        self._hilo = None
        self._hilo_biblioteca = None
        self._generacion = 0          # sube al cambiar de carpeta: frena el análisis anterior
        self._sonadas = set()
        self.siguiente = None         # título de la que viene
        self.mensaje = ""
        self._ultima_mezcla = None    # registro de la última transición que empezó a sonar
        self._caracteristicas_de = {}  # id(registro) -> lo que vio el juez, para aprender del voto
        self.nivel = (0.0, 0.0)
        # Plan de la sesión: bloques en orden. genero None = variado (el DJ decide);
        # limite None = sin límite, ("min", 30) o ("canciones", 5). El último bloque no se termina.
        self.plan = [{"genero": None, "limite": None}]
        self.preferir_artista = False
        self.estilo = None            # resumen de un perfil aprendido de un DJ (dj_aprender), o None
        self._bloque = 0
        self._bloque_de = {}
        self._bloque_inicio = 0.0
        self._bloque_canciones = 0
        self._racha = 0               # modo variado: canciones seguidas del mismo género...
        self._racha_objetivo = 4      # ...y cuántas se quiere antes de cambiar
        self.genero_prioridad = None  # el género que el usuario eligió: se prepara primero
        self._pedido = None           # copiloto: {indice, entrar_s} que eligió el DJ
        self._actual = None           # (indice, audio, análisis, tramo) de la canción que suena
        # copiloto sin espera: ruta -> {audio, analisis, regiones: [(desde, hasta, pistas)]} que se
        # preparan en segundo plano (inicio de las recomendadas, tramos de la actual en cada punto)
        self._anticipado = {}
        self._tiempo_preparar = 70.0  # segundos que tarda una mezcla sin nada anticipado (se mide)
        self._anticipando = False
        self.salida_publico = None      # id de la salida de audio para el público (None = la del sistema)
        self.salida_auriculares = None  # id de la salida para pre-escuchar la mezcla (None = sin pre-escucha)
        self._letra = {}              # ruta -> [(inicio_s, fin_s, palabra)] de la voz (Whisper)
        self._letra_hasta = {}        # ruta -> hasta qué segundo ya se escuchó la letra
        self._escala_timbre = None    # escala de la huella de percusión en esta biblioteca
        # el equipo del cliente: sin medir todavía se asume uno modesto; la medición real se hace
        # en segundo plano la primera vez y queda guardada
        self.equipo = dj_equipo.perfil(medir=False)
        if self.equipo.get("segundos_por_segundo") is None:
            threading.Thread(target=self._medir_equipo, daemon=True).start()

    # ---------- biblioteca ----------

    def cargar_carpeta(self, carpeta):
        self._generacion += 1
        self._escala_timbre = None
        self.carpeta = carpeta
        rutas = dj_analisis.listar_canciones(carpeta)
        self.biblioteca = []
        for ruta in rutas:
            # El género sale de la carpeta donde está la canción (USB/Salsa/..., USB/Rock/...): es la
            # clasificación de quien armó el USB. Si está suelta en la raíz, se usa la etiqueta del archivo.
            relativa = os.path.relpath(os.path.dirname(ruta), carpeta)
            carpeta_genero = None if relativa == "." else relativa.split(os.sep)[0]
            artista, titulo = dj_analisis.de_nombre(ruta)
            cancion = {"ruta": ruta, "titulo": titulo, "artista": artista, "genero": carpeta_genero or "Sin género",
                       "de_carpeta": carpeta_genero is not None, "bpm": None, "tono": None, "camelot": None,
                       "energia": None, "dur": None, "error": None}
            try:
                guardado = dj_analisis.en_cache(ruta)
                if guardado and guardado.get("v") == dj_analisis.VERSION_ANALISIS:
                    self._aplicar(cancion, guardado)
            except OSError:
                pass
            self.biblioteca.append(cancion)
        self._hilo_biblioteca = threading.Thread(target=self._analizar_biblioteca, args=(self._generacion,),
                                                 daemon=True)
        self._hilo_biblioteca.start()
        return len(rutas)

    @staticmethod
    def _aplicar(cancion, datos):
        """Pasa a la canción lo que dio el análisis (y las etiquetas del archivo, donde falte algo)."""
        for clave in ("bpm", "tono", "camelot", "energia", "dur", "patron", "timbre"):
            cancion[clave] = datos.get(clave)
        # El nombre del archivo («Artista - Título.mp3») manda: las etiquetas internas de los MP3
        # suelen venir mal (otro artista, «lyrics», «Official Music Video»). Solo se usan si el
        # nombre no trae el artista.
        if not cancion["artista"]:
            cancion["artista"] = datos.get("artista_tag")
            cancion["titulo"] = datos.get("titulo_tag") or cancion["titulo"]
        if not cancion["de_carpeta"] and datos.get("genero_tag"):
            cancion["genero"] = datos["genero_tag"]

    def generos(self):
        """{género: cantidad de canciones}, para armar el plan de la sesión."""
        cuenta = {}
        for c in self.biblioteca:
            if c["error"] is None:
                cuenta[c["genero"]] = cuenta.get(c["genero"], 0) + 1
        return dict(sorted(cuenta.items()))

    def _analizar_una(self, cancion):
        if cancion["bpm"] is None and cancion["error"] is None:
            try:
                self._aplicar(cancion, dj_analisis.analisis_rapido(cancion["ruta"]))
            except Exception as error:
                cancion["error"] = str(error)

    def _analizar_biblioteca(self, generacion):
        """Prepara la música en segundo plano, apenas se conecta el USB: varios procesos a la vez
        (según el equipo del cliente) y primero el género que se eligió, así la fiesta puede
        empezar sin esperar a que esté todo. Mientras se arma una mezcla, cede el procesador."""
        from concurrent.futures import ProcessPoolExecutor, wait

        self.preparacion = {"inicio": time.time(), "hechas": 0}
        procesos = max(1, int(self.equipo.get("procesos", 1)))
        try:
            pool = ProcessPoolExecutor(max_workers=procesos) if procesos > 1 else None
        except Exception:  # noqa: BLE001
            pool = None
        try:
            while generacion == self._generacion:
                pendientes = [c for c in self.biblioteca if c["bpm"] is None and c["error"] is None]
                if not pendientes:
                    break
                while self.mensaje.startswith("Preparando") and generacion == self._generacion:
                    time.sleep(0.5)
                # primero el género elegido (puede cambiar mientras tanto: se mira en cada tanda)
                pendientes.sort(key=lambda c: c["genero"] != self.genero_prioridad)
                tanda = pendientes[:procesos * 2]
                if pool is None:
                    for cancion in tanda:
                        self._analizar_una(cancion)
                else:
                    futuros = {pool.submit(dj_analisis.analizar_seguro, c["ruta"]): c for c in tanda}
                    wait(futuros)
                    for futuro, cancion in futuros.items():
                        datos, error = futuro.result()
                        if datos:
                            dj_analisis.recordar(cancion["ruta"], datos)
                            self._aplicar(cancion, datos)
                        else:
                            cancion["error"] = error
                self.preparacion["hechas"] += len(tanda)
                dj_analisis.guardar_cache()
                self._al_evento("biblioteca", "")
        finally:
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)
        dj_analisis.guardar_cache()
        self._escala_timbre = None  # con toda la biblioteca analizada, la escala es más fiel
        if generacion == self._generacion:
            self._al_evento("biblioteca", "lista")

    def analizadas(self):
        return sum(1 for c in self.biblioteca if c["bpm"] is not None)

    def preparadas(self):
        """Canciones ya revisadas (analizadas o descartadas por estar dañadas)."""
        return sum(1 for c in self.biblioteca if c["bpm"] is not None or c["error"] is not None)

    def listas_del_genero(self, genero):
        return sum(1 for c in self.biblioteca if c["bpm"] is not None and (genero is None or c["genero"] == genero))

    def segundos_restantes(self):
        """Estimación de lo que falta para tener todo el USB preparado (None si aún no se sabe)."""
        p = getattr(self, "preparacion", None)
        faltan = len(self.biblioteca) - self.preparadas()
        if not p or p["hechas"] < 4 or faltan <= 0:
            return None if faltan > 0 else 0
        return faltan * (time.time() - p["inicio"]) / p["hechas"]

    def _medir_equipo(self):
        """Mide una vez cuánto tarda este equipo en separar pistas (se guarda para las próximas)."""
        try:
            self.equipo = dj_equipo.perfil()
            self._al_evento("equipo", self.equipo.get("detalle", ""))
        except Exception:  # noqa: BLE001
            pass

    # ---------- elegir la siguiente ----------

    def _avanzar_bloque(self):
        self._bloque += 1
        self._bloque_inicio, self._bloque_canciones = time.time(), 0

    def _genero_del_plan(self):
        """Género del bloque actual (None = variado). Si el bloque ya cumplió su tiempo o su cantidad
        de canciones, pasa al siguiente."""
        while self._bloque < len(self.plan) - 1:
            limite = self.plan[self._bloque]["limite"]
            if limite is None:
                break
            tipo, cantidad = limite
            cumplido = ((time.time() - self._bloque_inicio) / 60.0 >= cantidad if tipo == "min"
                        else self._bloque_canciones >= cantidad)
            if not cumplido:
                break
            self._avanzar_bloque()
        return self.plan[self._bloque]["genero"]

    def _elegir(self, actual, bpm_actual):
        """La siguiente canción, con criterio de DJ: respeta el plan (género y duración), y dentro
        de lo permitido prefiere tempo que se pueda igualar, tonalidad compatible, energía
        parecida y (si se pidió) el mismo artista. Algo de azar hace cada sesión distinta."""
        pendientes = [i for i, c in enumerate(self.biblioteca)
                      if i != actual and i not in self._sonadas and c["error"] is None]
        if not pendientes:
            return None
        cancion_actual = self.biblioteca[actual] if actual is not None else None
        genero_actual = cancion_actual["genero"] if cancion_actual else None

        genero = self._genero_del_plan()
        if genero is not None:
            del_genero = [i for i in pendientes if self.biblioteca[i]["genero"] == genero]
            while not del_genero and self._bloque < len(self.plan) - 1:  # se acabó ese género: al siguiente bloque
                self._avanzar_bloque()
                genero = self.plan[self._bloque]["genero"]
                del_genero = [i for i in pendientes if genero is None or self.biblioteca[i]["genero"] == genero]
            pendientes = del_genero or pendientes
        if genero is None and genero_actual is not None:
            # Variado: unas canciones seguidas del mismo género y después cambia a otro, para que
            # la sesión tenga tramos con sentido y no salte de género en cada canción.
            mismo = [i for i in pendientes if self.biblioteca[i]["genero"] == genero_actual]
            otros = [i for i in pendientes if self.biblioteca[i]["genero"] != genero_actual]
            if self._racha < self._racha_objetivo and mismo:
                pendientes = mismo
            elif otros:
                pendientes = otros

        # analizar candidatas hasta tener de dónde elegir (la biblioteca puede estar a medio analizar)
        con_datos = [i for i in pendientes if self.biblioteca[i]["bpm"] is not None]
        faltan = [i for i in pendientes if self.biblioteca[i]["bpm"] is None]
        for i in self._rng.permutation(faltan)[:max(0, 14 - len(con_datos))]:
            self._analizar_una(self.biblioteca[int(i)])
        con_datos = [i for i in pendientes if self.biblioteca[i]["bpm"] is not None]
        if not con_datos:
            return None
        if bpm_actual is None:  # primera canción de la sesión
            return int(self._rng.choice(con_datos))

        recomendadas = self._puntuar(actual, bpm_actual, con_datos, azar=True)
        return recomendadas[0]["indice"]

    def _puntuar(self, actual, bpm_actual, candidatas, azar=False):
        """Ordena las candidatas con criterio de DJ y explica cada puntaje en palabras:
        tempo que se pueda igualar, tonalidad compatible, batería parecida (huella de percusión),
        energía parecida y, si se pidió, el mismo artista. Devuelve [{indice, puntaje, razones}]."""
        cancion_actual = self.biblioteca[actual]
        if self._escala_timbre is None:
            self._escala_timbre = dj_analisis.escala_timbre(self.biblioteca)
        lista = []
        for i in candidatas:
            c = self.biblioteca[i]
            razones = []
            paso_a, paso_b, diferencia = dj_motor.relacion_tempo(bpm_actual, c["bpm"])
            puntaje = -10.0 * abs(np.log1p(diferencia))                                 # tempo parecido
            if dj_motor.compatibles(bpm_actual, c["bpm"]):
                puntaje += 1.0                                                          # se puede sincronizar
                cambio = 100.0 * (c["bpm"] * (paso_a / paso_b) / bpm_actual - 1.0)
                razones.append(f"{c['bpm']:.0f} BPM ({cambio:+.0f} %)" +
                               (" · doble/mitad de tempo" if paso_a != paso_b else ""))
            else:
                razones.append(f"{c['bpm']:.0f} BPM: tempo muy distinto (corte con eco)")
            if dj_analisis.camelot_compatibles(cancion_actual["camelot"] or "", c["camelot"] or ""):
                puntaje += 0.6                                                          # tonalidad compatible
                razones.append(f"tonalidad compatible ({cancion_actual['camelot']} → {c['camelot']})")
            parecido = dj_analisis.parecido_percusion(cancion_actual, c, self._escala_timbre)
            if parecido is not None:
                puntaje += 3.0 * (parecido - 0.5)                                       # batería parecida
                if parecido >= 0.6:
                    razones.append(f"batería {100 * parecido:.0f} % parecida: buena para mezclar por la batería")
            if cancion_actual["energia"] is not None and c["energia"] is not None:
                delta = c["energia"] - cancion_actual["energia"]
                puntaje -= 0.06 * abs(delta)                                            # energía parecida
                razones.append("misma energía" if abs(delta) < 2 else
                               ("sube la energía" if delta > 0 else "baja la energía"))
            if c["artista"] and c["artista"] == cancion_actual["artista"]:
                razones.append("mismo artista")
                if self.preferir_artista:
                    puntaje += 1.0
            if azar:
                puntaje += self._rng.uniform(0.0, 0.5)                                  # cada sesión, distinta
            lista.append({"indice": i, "puntaje": puntaje, "razones": razones, "parecido": parecido})
        lista.sort(key=lambda r: -r["puntaje"])
        return lista

    def recomendar(self, cuantas=5):
        """Las mejores canciones para seguir a la que suena, con sus razones (el copiloto).
        Respeta el plan de géneros. Lista vacía si no suena nada."""
        with self._candado:
            actual = next((t for t in self._tramos if t["tipo"] == "cancion"), None)
        if actual is None:
            return []
        indice = actual["indice"]
        bpm = (actual.get("analisis") or {}).get("bpm") or self.biblioteca[indice]["bpm"]
        if bpm is None:
            return []
        pendientes = [i for i, c in enumerate(self.biblioteca)
                      if i != indice and i not in self._sonadas and c["error"] is None and c["bpm"] is not None]
        genero = self._genero_del_plan()
        if genero is not None:
            pendientes = [i for i in pendientes if self.biblioteca[i]["genero"] == genero] or pendientes
        return self._puntuar(indice, bpm, pendientes)[:cuantas]

    def _anotar_eleccion(self, anterior, nueva):
        """Lleva la cuenta del bloque y de la racha de género tras elegir una canción."""
        self._bloque_canciones += 1
        self._bloque_de[nueva] = self._bloque  # a qué parte del plan pertenece esta canción
        if anterior is not None and self.biblioteca[anterior]["genero"] == self.biblioteca[nueva]["genero"]:
            self._racha += 1
        else:
            self._racha, self._racha_objetivo = 1, int(self._rng.integers(3, 6))

    def describir_plan(self):
        partes = []
        for k, bloque in enumerate(self.plan):
            limite = bloque["limite"]
            cuanto = ("sin límite" if limite is None else
                      f"{limite[1]:g} min" if limite[0] == "min" else f"{limite[1]} canciones")
            partes.append(f"{k + 1}) {bloque['genero'] or 'Variado'} · {cuanto}")
        return "   →   ".join(partes)

    # ---------- reproducción ----------

    def empezar(self, indice=None):
        if sd is None:
            raise RuntimeError("Falta el paquete sounddevice para reproducir.")
        self.detener()
        if not self.biblioteca:
            raise RuntimeError("Primero elige una carpeta o USB con música.")
        self._parar = threading.Event()
        self._sonadas = set()
        self._bloque, self._bloque_inicio, self._bloque_canciones = 0, time.time(), 0
        self._bloque_de = {}
        self._racha, self._racha_objetivo = 0, int(self._rng.integers(3, 6))
        if indice is None:  # sin canción elegida: una del primer género del plan
            indice = self._elegir(None, None)
            if indice is None:
                raise RuntimeError("No hay canciones que se puedan reproducir con ese plan.")
        self._anotar_eleccion(None, indice)
        self._hilo = threading.Thread(target=self._trabajo, args=(indice, self._parar), daemon=True)
        self._hilo.start()

    def detener(self):
        self._parar.set()
        flujo, self._stream = self._stream, None
        if flujo is not None:
            try:
                flujo.stop()
                flujo.close()
            except Exception:
                pass
        with self._candado:
            self._tramos = []
        self.siguiente, self.mensaje, self.nivel = None, "", (0.0, 0.0)

    @property
    def activa(self):
        return self._stream is not None

    def _cargar(self, indice):
        cancion = self.biblioteca[indice]
        anticipada = self._anticipado.get(cancion["ruta"])
        if anticipada and anticipada.get("audio") is not None:
            return anticipada["audio"], anticipada["analisis"]
        audio = dj_analisis.decodificar(cancion["ruta"])
        analisis = dj_analisis.analisis_completo(audio)
        cancion["bpm"] = round(analisis["bpm"], 1)
        return audio, analisis

    def _tramo_cancion(self, indice, audio, desde=0):
        return {"tipo": "cancion", "indice": indice, "titulo": self.biblioteca[indice]["titulo"],
                "audio": audio, "pos": int(desde), "fin": len(audio), "desde_s": desde / SR}

    def _trabajo(self, indice, parar):
        try:
            self.mensaje = "Cargando la primera canción..."
            self._al_evento("estado", "")
            audio_a, ana_a = self._cargar(indice)
        except Exception as error:
            self.biblioteca[indice]["error"] = str(error)
            self.mensaje = ""
            self._al_evento("error", f"No se pudo cargar «{self.biblioteca[indice]['titulo']}»:\n{error}")
            return
        if parar.is_set():
            return
        tramo_a = self._tramo_cancion(indice, audio_a, desde=int(ana_a["inicio_s"] * SR))
        tramo_a["analisis"] = ana_a
        with self._candado:
            self._tramos = [tramo_a]
        self._sonadas.add(indice)
        try:
            self._stream = sd.OutputStream(samplerate=SR, channels=2, dtype="float32", callback=self._callback,
                                           blocksize=2048, latency=0.25, device=self.salida_publico)
            self._stream.start()
        except Exception as error:
            self._stream = None
            self._al_evento("error", f"No se pudo abrir la salida de audio:\n{error}")
            return

        while not parar.is_set():
            self._actual = (indice, audio_a, ana_a, tramo_a)
            self._al_evento("recomendaciones", "")
            # solo se guarda lo de la canción actual; lo de las demás se vuelve a anticipar
            actual_ruta = self.biblioteca[indice]["ruta"]
            self._anticipado = {r: v for r, v in self._anticipado.items() if r == actual_ruta}
            self._anticipando = True
            threading.Thread(target=self._anticipar, args=(tramo_a, parar), daemon=True).start()
            # primero se anticipa (recomendadas y puntos); la mezcla automática espera, salvo que
            # se acerque el final de la canción o el DJ elija algo
            while (self._anticipando and not parar.is_set() and self._pedido is None and
                   (tramo_a["fin"] - tramo_a["pos"]) / SR > self._tiempo_preparar + 45.0):
                time.sleep(0.5)
            tramo_b = None
            if self._pedido is None:
                tramo_b = self._preparar_siguiente(indice, audio_a, ana_a, tramo_a, parar)
            if tramo_b is None and self._pedido and not parar.is_set():
                # el DJ eligió mientras se preparaba la automática: se atiende ya su pedido
                pedido, self._pedido = self._pedido, None
                tramo_b = self._preparar_siguiente(indice, audio_a, ana_a, tramo_a, parar,
                                                   indice_b=pedido.get("indice"), entrar_s=pedido.get("entrar_s"))
            if tramo_b is None:
                break
            # esperar a que B sea lo que suena; ahí A ya no se necesita y se descarta. Mientras
            # tanto, si el DJ elige otra canción o dónde cambiar (copiloto), la mezcla se rehace.
            while not parar.is_set():
                with self._candado:
                    actual = self._tramos[0] if self._tramos else None
                if actual is tramo_b or actual is None:
                    break
                pedido, self._pedido = self._pedido, None
                if pedido and actual is tramo_a:
                    anterior = tramo_b["indice"]
                    nuevo = self._preparar_siguiente(indice, audio_a, ana_a, tramo_a, parar,
                                                     indice_b=pedido.get("indice", anterior),
                                                     entrar_s=pedido.get("entrar_s"))
                    if nuevo is not None:
                        if nuevo["indice"] != anterior:
                            self._sonadas.discard(anterior)
                        tramo_b = nuevo
                time.sleep(0.3)
            self._pedido = None
            indice, audio_a, ana_a, tramo_a = tramo_b["indice"], tramo_b["audio"], tramo_b["analisis"], tramo_b
        self.siguiente = None
        if not parar.is_set():
            self.mensaje = "No quedan más canciones para mezclar."
            self._al_evento("estado", "")

    def _anticipar(self, tramo_a, parar):
        """Mientras suena una canción, deja hecho lo lento (decodificar, analizar y separar con
        la IA) para las 3 primeras recomendadas y para los tramos de la actual en cada punto de
        cambio. Así, si el DJ elige una de esas, la mezcla queda lista en segundos.
        Cede el procesador mientras se arma una mezcla de verdad."""
        if not self.equipo.get("separar_con_ia", True):
            self._anticipando = False
            return  # en modo ligero separar es instantáneo: no hace falta anticipar

        def sigue():
            return not parar.is_set() and self._actual is not None and self._actual[3] is tramo_a

        def esperar_turno():
            while sigue() and self.mensaje.startswith("Preparando"):
                time.sleep(0.5)
            return sigue()

        def separar(ruta, audio, desde_s, hasta_s):
            a, b = max(0, int(desde_s * SR)), min(len(audio), int(hasta_s * SR))
            entrada = self._anticipado.setdefault(ruta, {"audio": None, "analisis": None, "regiones": []})
            if b - a < SR or any(r0 <= a and b <= r1 for r0, r1, _ in entrada["regiones"]):
                return
            if not esperar_turno():
                return
            pistas = dj_motor.separar_tramo(audio[a:b])
            entrada["regiones"].append((a, b, pistas))

        try:
            self._anticipar_todo(tramo_a, sigue, esperar_turno, separar)
        finally:
            self._anticipando = False

    def _anticipar_todo(self, tramo_a, sigue, esperar_turno, separar):
        time.sleep(3.0)  # que arranque bien la canción
        indice, audio_a, ana_a = tramo_a["indice"], tramo_a["audio"], tramo_a["analisis"]
        ruta_a = self.biblioteca[indice]["ruta"]
        largo_max = 2.0 * dj_motor.BEATS * 60.0 / ana_a["bpm"]  # con doble tempo, el doble de beats
        try:
            recomendadas = [r["indice"] for r in self.recomendar(3)]
            # 1) el inicio de las recomendadas (donde entran) y 2) la actual en el punto más cercano
            for i in recomendadas:
                if not esperar_turno():
                    return
                ruta = self.biblioteca[i]["ruta"]
                entrada = self._anticipado.setdefault(ruta, {"audio": None, "analisis": None, "regiones": []})
                if entrada["audio"] is None:
                    entrada["audio"] = dj_analisis.decodificar(ruta)
                    entrada["analisis"] = dj_analisis.analisis_completo(entrada["audio"])
                inicio = entrada["analisis"]["inicio_s"]
                separar(ruta, entrada["audio"], inicio - 2.0, inicio + largo_max + 6.0)
            self._al_evento("recomendaciones", "listo")
            # 3) la canción actual, separada de corrido y por delante de lo que suena: así cualquier
            #    punto de cambio futuro ya tiene hecho lo lento. Tramos de 30 s que se empalman con
            #    2 s de fundido; si la reproducción lo alcanza, se reinicia más adelante.
            entrada = self._anticipado.setdefault(ruta_a, {"audio": audio_a, "analisis": ana_a, "regiones": []})
            region = None
            fin_musica = int(ana_a["fin_s"] * SR)
            while sigue():
                ahora = tramo_a["pos"]
                if region is None or region[1] < ahora + 20 * SR:
                    region = None
                    desde = min(ahora + 20 * SR, fin_musica)
                else:
                    desde = region[1] - 2 * SR
                if desde >= fin_musica - 5 * SR:
                    break
                hasta = min(len(audio_a), desde + 32 * SR)
                if not esperar_turno():
                    return
                pistas = dj_motor.separar_tramo(audio_a[desde:hasta])
                if region is None:
                    region = (desde, hasta, pistas)
                else:
                    r0, r1, viejas = region
                    solapa = r1 - desde
                    rampa = np.linspace(0.0, 1.0, solapa, dtype=np.float32)[:, None]
                    unidas = {}
                    for k in pistas:
                        cola = viejas[k][-solapa:] * (1.0 - rampa) + pistas[k][:solapa] * rampa
                        unidas[k] = np.concatenate([viejas[k][:-solapa], cola, pistas[k][solapa:]])
                    region = (r0, hasta, unidas)
                entrada["regiones"] = [region]
                # la letra de este tramo (Whisper sobre la voz ya separada): dónde termina cada verso
                if dj_letra.disponible() and sigue():
                    nuevo_desde = max(desde, int(self._letra_hasta.get(ruta_a, 0.0) * SR))
                    if hasta - nuevo_desde > 2 * SR:
                        voz = region[2]["voz"][nuevo_desde - region[0]:hasta - region[0]]
                        self._letra.setdefault(ruta_a, []).extend(dj_letra.palabras(voz, SR, nuevo_desde / SR))
                        self._letra_hasta[ruta_a] = hasta / SR
                self._al_evento("recomendaciones", "")
        except Exception:  # noqa: BLE001  (anticipar es una ayuda: si falla, se prepara como siempre)
            pass

    def anticipada(self, indice):
        """True si lo lento de esta canción ya está hecho (para marcarla en el copiloto)."""
        entrada = self._anticipado.get(self.biblioteca[indice]["ruta"])
        return bool(entrada and entrada.get("regiones"))

    def pedir(self, indice=None, entrar_s=None):
        """El copiloto: el DJ elige la siguiente canción (indice) y/o dónde cambiar (entrar_s,
        segundo de la canción que suena). La mezcla se rehace con eso mientras suena la actual."""
        self._pedido = {k: v for k, v in (("indice", indice), ("entrar_s", entrar_s)) if v is not None}

    def puntos_de_cambio(self):
        """Dónde conviene cambiar en la canción que suena, desde ahora: inicios de frase a los que
        se llega a tiempo de preparar la mezcla. Hasta 3: el más cercano, donde la canción baja de
        intensidad (un respiro: buen lugar para entrar) y el final. [{segundo, motivo}]"""
        if self._actual is None:
            return []
        indice, audio, ana, tramo = self._actual
        ahora = tramo["pos"] / SR
        lento = (self._tiempo_preparar + 10.0) if self.equipo.get("separar_con_ia", True) else 15.0
        regiones = self._anticipado.get(self.biblioteca[indice]["ruta"], {}).get("regiones") or []
        beats, fase = ana["beats"], ana["fase"]
        largo = dj_motor.BEATS * 60.0 / ana["bpm"]

        def margen(t):  # cuánto antes hay que pedirlo: poco si ese tramo ya está separado
            a, b = int((t - 2.0) * SR), int((t + 2.0 * largo + 3.0) * SR)
            return 30.0 if any(r0 <= a and b <= r1 for r0, r1, _ in regiones) else lento

        frases = [float(beats[k]) for k in range(fase % 4, len(beats), dj_motor.FRASE)
                  if ahora + margen(beats[k]) <= beats[k] <= ana["fin_s"] - largo - 2.0]
        if not frases:
            return []
        # la letra: no ofrecer puntos donde la voz de A se apagaría a mitad de una frase cantada
        # (las recetas bajan la voz de A entre el 20 % y el 55 % de la transición)
        ruta = self.biblioteca[indice]["ruta"]
        letra, escuchada = self._letra.get(ruta, []), self._letra_hasta.get(ruta, 0.0)

        def sobre_la_letra(t):
            """None si esa parte aún no se escuchó; si no, (corta_la_letra, termina_un_verso)."""
            a, b = t + 0.2 * largo, t + 0.55 * largo
            if b > escuchada:
                return None
            fin = next((t + x * largo for x in (0.2, 0.3, 0.4) if dj_letra.fin_de_verso(letra, t + x * largo)), None)
            return dj_letra.cantando(letra, a, b) > 0.35 and fin is None, fin

        frases = [t for t in frases if not (sobre_la_letra(t) or (False,))[0]]
        if not frases:
            return []

        def detalle(t):
            info = sobre_la_letra(t)
            if info is None or info[1] is None:
                return ""
            ultimas = dj_letra.ultimas_palabras(letra, info[1])
            return f" · termina un verso: «…{ultimas}»" if ultimas else " · la voz descansa"

        mono = audio.mean(axis=1)
        def energia(t):
            a, b = int(t * SR), int((t + 8 * 60.0 / ana["bpm"]) * SR)
            return float(np.sqrt(np.mean(np.square(mono[a:b], dtype=np.float64))) + 1e-9)
        media = float(np.median([energia(t) for t in frases]))
        puntos = [{"segundo": frases[0], "motivo": "el más cercano (inicio de frase)" + detalle(frases[0])}]
        respiro = min(frases[1:-1] or frases, key=energia)
        if respiro != frases[0] and energia(respiro) < 0.8 * media:
            puntos.append({"segundo": respiro,
                           "motivo": "la canción baja de intensidad: buen momento para entrar" + detalle(respiro)})
        if frases[-1] not in (p["segundo"] for p in puntos):
            puntos.append({"segundo": frases[-1], "motivo": "al final, en su última frase"})
        return sorted(puntos, key=lambda p: p["segundo"])

    def _preparar_siguiente(self, indice, audio_a, ana_a, tramo_a, parar, indice_b=None, entrar_s=None):
        """Elige B (o usa la que pidió el DJ), arma la transición (al final, o desde entrar_s) y la
        encola. Devuelve el tramo de B, o None si no hay más."""
        elegida = indice_b
        for _intento in range(1 if elegida is not None else 4):
            if parar.is_set():
                return None
            indice_b = elegida if elegida is not None else self._elegir(indice, ana_a["bpm"])
            if indice_b is None:
                return None
            cancion_b = self.biblioteca[indice_b]
            self.siguiente = cancion_b["titulo"]
            self.mensaje = f"Preparando la mezcla con «{cancion_b['titulo']}»..."
            self._al_evento("estado", "")
            try:
                inicio = time.perf_counter()
                audio_b, ana_b = self._cargar(indice_b)
                pesos, beats, salir_s = pesos_recetas(), dj_motor.BEATS, None
                if self.estilo:  # mezclar como el DJ del que se aprendió
                    beats = self.estilo["beats_motor"]
                    # cuánto deja sonar cada canción (mínimo 50 s: hay que alcanzar a preparar la mezcla)
                    salir_s = tramo_a["desde_s"] + max(self.estilo["segundos_por_cancion"], 50.0)
                    t = self.estilo["tecnicas"]
                    gusto = {"eco": 0.3 + 2 * t["corte"], "cambio_de_bajo": 0.3 + 2 * t["graves"],
                             "mezcla_larga": 0.3 + t["fundido"] + t["graves"],
                             "sin_bateria": 0.3 + t["graves"] + t["fundido"], "voz_sobre_ritmo": 0.3 + 2 * t["fundido"]}
                    pesos = {r: pesos.get(r, 0.5) * g for r, g in gusto.items()}
                if entrar_s is not None:  # el DJ eligió dónde cambiar: el ritmo de A se mide ahí
                    salir_s = entrar_s + 2.0 * beats * 60.0 / ana_a["bpm"] + 3.0
                # el ritmo se vuelve a medir, fino, justo donde se mezcla (final de A, inicio de B),
                # y el juez (IA entrenada) elige la lectura del ritmo de B que va a quedar en ritmo
                caracteristicas, nota_juez = None, None
                try:
                    fin_a, opciones = dj_juez.candidatos(audio_a, ana_a, audio_b, ana_b,
                                                         self.biblioteca[indice], cancion_b, salir_s)
                    ini_b, nota_juez = dj_juez.elegir(fin_a, opciones)
                    caracteristicas = next(c for o, c in opciones if o is ini_b)
                except Exception:  # noqa: BLE001
                    fin_a, ini_b = dj_motor.afinar(audio_a, ana_a, audio_b, ana_b, salir_s)
                ligero = not self.equipo.get("separar_con_ia", True)  # equipo modesto: sin IA de separación
                if ligero:
                    pesos = {r: (pesos.get(r, 0.5) if r in dj_motor.RECETAS_LIGERAS else 0.0)
                             for r in dj_motor.SINCRONIZADAS}
                plan = dj_motor.planear(fin_a, ini_b, self._rng, pesos, beats=beats, salir_s=salir_s,
                                        entrar_s=entrar_s)
                plan["ligero"] = ligero
                plan["regiones_a"] = self._anticipado.get(self.biblioteca[indice]["ruta"], {}).get("regiones")
                plan["regiones_b"] = self._anticipado.get(cancion_b["ruta"], {}).get("regiones")
                sin_anticipar = not plan["regiones_a"] or not plan["regiones_b"]
                # una elección del DJ (copiloto) interrumpe la preparación automática
                interrumpir = (lambda: parar.is_set()) if elegida is not None else                     (lambda: parar.is_set() or self._pedido is not None)
                resultado = dj_motor.preparar(audio_a, fin_a, audio_b, ini_b, plan, cancelado=interrumpir)
                if resultado is None:
                    return None
            except Exception as error:
                cancion_b["error"] = str(error)
                self._al_evento("aviso", f"No se pudo mezclar con «{cancion_b['titulo']}»: {error}")
                continue

            self._sonadas.add(indice_b)
            self._anotar_eleccion(indice, indice_b)
            a = self.biblioteca[indice]
            registro = {"fecha": time.strftime("%Y-%m-%d %H:%M:%S"), "a": a["titulo"], "b": cancion_b["titulo"],
                        "bpm_a": round(ana_a["bpm"], 2), "bpm_b": round(ana_b["bpm"], 2),
                        "camelot_a": a["camelot"], "camelot_b": cancion_b["camelot"],
                        "genero_a": a["genero"], "genero_b": cancion_b["genero"],
                        "energia_a": a["energia"], "energia_b": cancion_b["energia"],
                        "receta": plan["receta"], "sincronizada": plan["sincronizada"], "beats": plan["beats"],
                        "estilo": bool(self.estilo), "nota_juez": nota_juez}
            self._caracteristicas_de[id(registro)] = (caracteristicas, cancion_b["titulo"])
            nombre = dj_motor.RECETAS[plan["receta"]]["nombre"]
            tramo_mezcla = {"tipo": "mezcla", "indice": indice_b, "titulo": f"{a['titulo']}  →  {cancion_b['titulo']}",
                            "audio": resultado["mezcla"], "pos": 0, "fin": len(resultado["mezcla"]),
                            "registro": registro, "receta": nombre}
            tramo_b = self._tramo_cancion(indice_b, audio_b, desde=resultado["b_continua"])
            tramo_b["analisis"] = ana_b
            with self._candado:
                a_tiempo = bool(self._tramos) and self._tramos[0] is tramo_a and tramo_a["pos"] < plan["a0"] - SR // 2
                if a_tiempo:
                    tramo_a["fin"] = plan["a0"]
                    self._tramos = [tramo_a, tramo_mezcla, tramo_b]
                else:  # la canción ya pasó el punto de mezcla: fundido corto al final de A, nunca de golpe
                    tramo_b["pos"] = int(ana_b["inicio_s"] * SR)
                    self._tramos = self._tramos[:1] + [tramo_b]
                    actual = self._tramos[0]
                    fin_a = min(int(ana_a["fin_s"] * SR), len(audio_a))
                    largo = 6 * SR
                    if actual is tramo_a and tramo_a["pos"] < fin_a - largo - SR // 2 and \
                            tramo_b["pos"] + largo < len(audio_b):
                        t = np.linspace(0.0, 0.5 * np.pi, largo, dtype=np.float32)[:, None]
                        fundido = (audio_a[fin_a - largo:fin_a] * np.cos(t)
                                   + audio_b[tramo_b["pos"]:tramo_b["pos"] + largo] * np.sin(t))
                        tramo_a["fin"] = fin_a - largo
                        tramo_b["pos"] += largo
                        registro["receta"], registro["sincronizada"] = "fundido_corto", False
                        tramo_fundido = {"tipo": "mezcla", "indice": indice_b, "audio": fundido.astype(np.float32),
                                         "titulo": tramo_mezcla["titulo"], "pos": 0, "fin": largo,
                                         "registro": registro, "receta": "Fundido corto"}
                        self._tramos = [tramo_a, tramo_fundido, tramo_b]
            segundos = time.perf_counter() - inicio
            if sin_anticipar:  # el copiloto ofrece puntos a los que se llega a tiempo de preparar
                self._tiempo_preparar = 0.7 * self._tiempo_preparar + 0.3 * segundos
            self.mensaje = (f"Lista: «{nombre}» ({segundos:.0f} s en prepararla)" if a_tiempo
                            else "La mezcla no llegó a tiempo: la siguiente entra directa.")
            self._al_evento("estado", "")
            return tramo_b
        return None

    def _callback(self, salida, cuadros, _tiempo, _estado):
        """Hilo de audio: copia lo que toca del tramo actual y pasa al siguiente cuando se acaba."""
        salida.fill(0.0)
        escritos = 0
        try:
            with self._candado:
                while escritos < cuadros and self._tramos:
                    tramo = self._tramos[0]
                    k = min(cuadros - escritos, tramo["fin"] - tramo["pos"])
                    if k <= 0:
                        self._tramos.pop(0)
                        continue
                    if tramo["tipo"] == "mezcla" and tramo["pos"] == 0:
                        self._ultima_mezcla = tramo["registro"]
                    salida[escritos:escritos + k] = tramo["audio"][tramo["pos"]:tramo["pos"] + k]
                    tramo["pos"] += k
                    escritos += k
            np.clip(salida, -1.0, 1.0, out=salida)
            self.nivel = (float(np.abs(salida[:, 0]).max()), float(np.abs(salida[:, 1]).max()))
        except Exception:
            salida.fill(0.0)

    # ---------- controles ----------

    def preescuchar(self):
        """Hace sonar la mezcla ya preparada SOLO por los auriculares, como un DJ antes de lanzarla.
        El público sigue escuchando la canción. Devuelve False si no hay mezcla lista o salida."""
        if self.salida_auriculares is None or self.salida_auriculares == self.salida_publico:
            return False
        with self._candado:
            mezcla = next((t for t in self._tramos if t["tipo"] == "mezcla"), None)
        if mezcla is None:
            return False
        sd.play(mezcla["audio"], SR, device=self.salida_auriculares)
        return True

    @staticmethod
    def parar_preescucha():
        sd.stop()  # detiene solo la pre-escucha: la salida del público es otro stream

    def mezclar_ya(self):
        """Salta a 5 s antes de la mezcla, si ya está lista."""
        with self._candado:
            if len(self._tramos) >= 2 and self._tramos[0]["tipo"] == "cancion" and self._tramos[1]["tipo"] == "mezcla":
                tramo = self._tramos[0]
                tramo["pos"] = max(tramo["pos"], tramo["fin"] - 5 * SR)
                return True
        return False

    def calificar(self, voto):
        """Guarda tu voto (+1 / -1) para la última transición que sonó. Devuelve su descripción o None."""
        registro = self._ultima_mezcla
        if registro is None:
            return None
        os.makedirs(dj_analisis.CARPETA_DATOS, exist_ok=True)
        with open(RUTA_VOTOS, "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(registro, voto=voto), ensure_ascii=False) + "\n")
        caracteristicas, cancion_b = self._caracteristicas_de.get(id(registro), (None, None))
        if caracteristicas:  # el voto se vuelve un ejemplo para reentrenar al juez
            import dj_entrenar
            dj_entrenar.guardar_voto(caracteristicas, cancion_b, voto)
            if len(dj_entrenar.leer_votos()) % 10 == 0:  # cada 10 votos aprende tu gusto (en segundo plano)
                threading.Thread(target=self._reentrenar_juez, daemon=True).start()
        self._ultima_mezcla = None  # un voto por transición
        return f"{registro['a']} → {registro['b']}"

    @staticmethod
    def _reentrenar_juez():
        try:
            from threadpoolctl import threadpool_limits
            with threadpool_limits(limits=1):
                dj_juez.entrenar(dj_juez.rutas_ejemplos(), dj_juez.votos_guardados())
        except Exception:  # noqa: BLE001
            pass

    def estado(self):
        """Foto para la interfaz: qué suena, por dónde va y qué viene."""
        with self._candado:
            if not self._tramos:
                return {"sonando": False, "mensaje": self.mensaje, "siguiente": self.siguiente,
                        "puede_votar": self._ultima_mezcla is not None}
            t = self._tramos[0]
            mezcla_lista = len(self._tramos) >= 2 and self._tramos[1]["tipo"] == "mezcla"
            c = self.biblioteca[t["indice"]] if t["indice"] < len(self.biblioteca) else {}
            return {"sonando": True, "tipo": t["tipo"], "titulo": t["titulo"], "indice": t["indice"],
                    "genero": c.get("genero"), "artista": c.get("artista"), "bloque": self._bloque_de.get(t["indice"], self._bloque),
                    "pos_s": t["pos"] / SR, "fin_s": t["fin"] / SR, "total_s": len(t["audio"]) / SR,
                    "receta": t.get("receta"), "siguiente": self.siguiente, "mensaje": self.mensaje,
                    "mezcla_lista": mezcla_lista and t["tipo"] == "cancion",
                    "puede_votar": self._ultima_mezcla is not None}
