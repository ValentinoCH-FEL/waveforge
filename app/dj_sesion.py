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
        self.nivel = (0.0, 0.0)

    # ---------- biblioteca ----------

    def cargar_carpeta(self, carpeta):
        self._generacion += 1
        self.carpeta = carpeta
        rutas = dj_analisis.listar_canciones(carpeta)
        self.biblioteca = []
        for ruta in rutas:
            cancion = {"ruta": ruta, "titulo": os.path.splitext(os.path.basename(ruta))[0],
                       "bpm": None, "tono": None, "camelot": None, "error": None}
            try:
                cancion.update(dj_analisis.en_cache(ruta) or {})
            except OSError:
                pass
            self.biblioteca.append(cancion)
        self._hilo_biblioteca = threading.Thread(target=self._analizar_biblioteca, args=(self._generacion,),
                                                 daemon=True)
        self._hilo_biblioteca.start()
        return len(rutas)

    def _analizar_una(self, cancion):
        if cancion["bpm"] is None and cancion["error"] is None:
            try:
                cancion.update(dj_analisis.analisis_rapido(cancion["ruta"]))
            except Exception as error:
                cancion["error"] = str(error)

    def _analizar_biblioteca(self, generacion):
        nuevas = 0
        for i, cancion in enumerate(self.biblioteca):
            if generacion != self._generacion:
                return
            if cancion["bpm"] is None and cancion["error"] is None:
                self._analizar_una(cancion)
                nuevas += 1
                if nuevas % 10 == 0:
                    dj_analisis.guardar_cache()
                self._al_evento("biblioteca", "")
            # mientras se prepara una mezcla, el análisis de fondo le deja el procesador
            while self.mensaje.startswith("Preparando") and generacion == self._generacion:
                time.sleep(0.5)
        dj_analisis.guardar_cache()
        self._al_evento("biblioteca", "lista")

    def analizadas(self):
        return sum(1 for c in self.biblioteca if c["bpm"] is not None)

    # ---------- elegir la siguiente ----------

    def _elegir(self, actual, bpm_actual):
        pendientes = [i for i, c in enumerate(self.biblioteca)
                      if i != actual and i not in self._sonadas and c["error"] is None]
        if not pendientes:
            return None
        con_datos = [i for i in pendientes if self.biblioteca[i]["bpm"] is not None]
        if len(con_datos) < min(6, len(pendientes)):  # la biblioteca aún se está analizando: adelantar algunas
            for i in self._rng.permutation([i for i in pendientes if i not in con_datos])[:6]:
                self._analizar_una(self.biblioteca[int(i)])
            con_datos = [i for i in pendientes if self.biblioteca[i]["bpm"] is not None]
        if not con_datos:
            return None
        camelot = self.biblioteca[actual]["camelot"] or ""
        puntajes = []
        for i in con_datos:
            c = self.biblioteca[i]
            puntaje = -10.0 * abs(np.log(c["bpm"] / bpm_actual))            # tempo parecido
            puntaje += 0.6 if dj_analisis.camelot_compatibles(camelot, c["camelot"] or "") else 0.0
            puntajes.append(puntaje + self._rng.uniform(0.0, 0.5))          # azar: cada sesión es distinta
        return con_datos[int(np.argmax(puntajes))]

    # ---------- reproducción ----------

    def empezar(self, indice=None):
        if sd is None:
            raise RuntimeError("Falta el paquete sounddevice para reproducir.")
        self.detener()
        if not self.biblioteca:
            raise RuntimeError("Primero elige una carpeta o USB con música.")
        if indice is None:
            indice = int(self._rng.integers(len(self.biblioteca)))
        self._parar = threading.Event()
        self._sonadas = set()
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
        audio = dj_analisis.decodificar(cancion["ruta"])
        analisis = dj_analisis.analisis_completo(audio)
        cancion["bpm"] = round(analisis["bpm"], 1)
        return audio, analisis

    def _tramo_cancion(self, indice, audio, desde=0):
        return {"tipo": "cancion", "indice": indice, "titulo": self.biblioteca[indice]["titulo"],
                "audio": audio, "pos": int(desde), "fin": len(audio)}

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
        with self._candado:
            self._tramos = [tramo_a]
        self._sonadas.add(indice)
        try:
            self._stream = sd.OutputStream(samplerate=SR, channels=2, dtype="float32", callback=self._callback,
                                           blocksize=2048, latency=0.25)
            self._stream.start()
        except Exception as error:
            self._stream = None
            self._al_evento("error", f"No se pudo abrir la salida de audio:\n{error}")
            return

        while not parar.is_set():
            tramo_b = self._preparar_siguiente(indice, audio_a, ana_a, tramo_a, parar)
            if tramo_b is None:
                break
            # esperar a que B sea lo que suena; ahí A ya no se necesita y se descarta
            while not parar.is_set():
                with self._candado:
                    actual = self._tramos[0] if self._tramos else None
                if actual is tramo_b or actual is None:
                    break
                time.sleep(0.3)
            indice, audio_a, ana_a, tramo_a = tramo_b["indice"], tramo_b["audio"], tramo_b["analisis"], tramo_b
        self.siguiente = None
        if not parar.is_set():
            self.mensaje = "No quedan más canciones para mezclar."
            self._al_evento("estado", "")

    def _preparar_siguiente(self, indice, audio_a, ana_a, tramo_a, parar):
        """Elige B, arma la transición y la encola. Devuelve el tramo de B, o None si no hay más."""
        for _intento in range(4):
            if parar.is_set():
                return None
            indice_b = self._elegir(indice, ana_a["bpm"])
            if indice_b is None:
                return None
            cancion_b = self.biblioteca[indice_b]
            self.siguiente = cancion_b["titulo"]
            self.mensaje = f"Preparando la mezcla con «{cancion_b['titulo']}»..."
            self._al_evento("estado", "")
            try:
                inicio = time.perf_counter()
                audio_b, ana_b = self._cargar(indice_b)
                plan = dj_motor.planear(ana_a, ana_b, self._rng, pesos_recetas())
                resultado = dj_motor.preparar(audio_a, ana_a, audio_b, ana_b, plan, cancelado=parar.is_set)
                if resultado is None:
                    return None
            except Exception as error:
                cancion_b["error"] = str(error)
                self._al_evento("aviso", f"No se pudo mezclar con «{cancion_b['titulo']}»: {error}")
                continue

            self._sonadas.add(indice_b)
            a = self.biblioteca[indice]
            registro = {"fecha": time.strftime("%Y-%m-%d %H:%M:%S"), "a": a["titulo"], "b": cancion_b["titulo"],
                        "bpm_a": round(ana_a["bpm"], 2), "bpm_b": round(ana_b["bpm"], 2),
                        "camelot_a": a["camelot"], "camelot_b": cancion_b["camelot"],
                        "receta": plan["receta"], "sincronizada": plan["sincronizada"]}
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
                else:  # la canción ya pasó el punto de mezcla: B entra cuando A termine, sin mezcla
                    tramo_b["pos"] = int(ana_b["inicio_s"] * SR)
                    self._tramos = self._tramos[:1] + [tramo_b]
            segundos = time.perf_counter() - inicio
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
        self._ultima_mezcla = None  # un voto por transición
        return f"{registro['a']} → {registro['b']}"

    def estado(self):
        """Foto para la interfaz: qué suena, por dónde va y qué viene."""
        with self._candado:
            if not self._tramos:
                return {"sonando": False, "mensaje": self.mensaje, "siguiente": self.siguiente,
                        "puede_votar": self._ultima_mezcla is not None}
            t = self._tramos[0]
            mezcla_lista = len(self._tramos) >= 2 and self._tramos[1]["tipo"] == "mezcla"
            return {"sonando": True, "tipo": t["tipo"], "titulo": t["titulo"], "indice": t["indice"],
                    "pos_s": t["pos"] / SR, "fin_s": t["fin"] / SR, "total_s": len(t["audio"]) / SR,
                    "receta": t.get("receta"), "siguiente": self.siguiente, "mensaje": self.mensaje,
                    "mezcla_lista": mezcla_lista and t["tipo"] == "cancion",
                    "puede_votar": self._ultima_mezcla is not None}
