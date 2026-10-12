"""
modo_datos.py — modo alterno: transformar datos numéricos de un CSV
usando el mismo motor nativo (requisitos opcionales 20-21).

Es una ventana aparte, accesible desde el menú "Herramientas" de
main.py. Lee una columna numérica de un CSV y le aplica un escalado
usando la función nativa `normalizar` (mismo motor, otro tipo de dato).
"""

import csv
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np


def _leer_columna_csv(ruta):
    with open(ruta, newline="", encoding="utf-8") as archivo:
        lector = csv.reader(archivo)
        filas = list(lector)

    if not filas:
        raise ValueError("El CSV está vacío.")

    # Si la primera fila no es numérica, se asume encabezado y se descarta.
    primera = filas[0]
    try:
        float(primera[0])
        datos = filas
    except ValueError:
        datos = filas[1:]

    if not datos:
        raise ValueError("No se encontraron filas de datos numéricos.")

    valores = np.array([float(fila[0]) for fila in datos], dtype=np.float32)
    return valores


def abrir_modo_datos(ventana_padre, motor_audio):
    """Abre la ventana del modo CSV. motor_audio es la instancia de MotorAudio ya cargada."""
    ventana = tk.Toplevel(ventana_padre)
    ventana.title("Modo de datos (CSV)")
    ventana.geometry("520x420")

    estado = {"valores": None, "resultado": None}

    etiqueta_archivo = ttk.Label(ventana, text="Ningún archivo cargado")
    etiqueta_archivo.pack(pady=(14, 6))

    def cargar():
        ruta = filedialog.askopenfilename(filetypes=[("CSV", "*.csv")])
        if not ruta:
            return
        try:
            estado["valores"] = _leer_columna_csv(ruta)
            etiqueta_archivo.config(text=f"{len(estado['valores'])} valores cargados")
            tabla.delete(*tabla.get_children())
            for i, v in enumerate(estado["valores"][:200]):
                tabla.insert("", "end", values=(i, f"{v:.4f}"))
        except (ValueError, OSError) as error:
            messagebox.showerror("Error al leer el CSV", str(error))

    def transformar():
        if estado["valores"] is None:
            messagebox.showwarning("Falta un archivo", "Carga un CSV primero.")
            return
        objetivo = float(control_nivel.get())
        try:
            # Reutiliza el mismo motor nativo que el modo de audio: la
            # transformación matemática (escalado) es la función normalizar.
            resultado = motor_audio.aplicar_mono("normalizar", estado["valores"], objetivo)
        except Exception as error:
            messagebox.showerror("Error al procesar", str(error))
            return

        estado["resultado"] = resultado
        tabla.delete(*tabla.get_children())
        for i, v in enumerate(resultado[:200]):
            tabla.insert("", "end", values=(i, f"{v:.4f}"))
        etiqueta_archivo.config(text=f"Transformado — nivel objetivo {objetivo:.2f}")

    barra = ttk.Frame(ventana)
    barra.pack(fill="x", padx=14)
    ttk.Button(barra, text="Cargar CSV", command=cargar).pack(side="left")
    ttk.Label(barra, text="Nivel objetivo:").pack(side="left", padx=(16, 4))
    control_nivel = tk.DoubleVar(value=0.95)
    ttk.Scale(barra, from_=0.1, to=1.0, variable=control_nivel, orient="horizontal").pack(
        side="left", fill="x", expand=True
    )
    ttk.Button(barra, text="Transformar", command=transformar).pack(side="left", padx=(8, 0))

    tabla = ttk.Treeview(ventana, columns=("indice", "valor"), show="headings", height=14)
    tabla.heading("indice", text="#")
    tabla.heading("valor", text="Valor")
    tabla.pack(fill="both", expand=True, padx=14, pady=14)
