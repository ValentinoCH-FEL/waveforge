"""
filtros_registry.py — catálogo de filtros disponibles.

Esta es la pieza que hace que el proyecto sea escalable: para agregar
un filtro nuevo solo hace falta:
  1. Escribir la función en native/filtros/<nombre>.cpp con la firma
     común (entrada, salida, cantidad, parametro) y declararla en
     filtro.h.
  2. Agregar una entrada aquí.
Nada en main.py ni en motor.py necesita cambiar.

Cada parámetro puede indicar "unidad" y "decimales" para mostrar su valor
actual junto al control (ej. "Umbral: -55 dB").
"""

FILTROS = {
    "ninguno": {
        "etiqueta": "Sin efecto",
        "descripcion": "La pista suena tal cual, sin ningún filtro.",
        "tipo": "ninguno",
        "funcion_nativa": None,
        "parametro": None,
    },
    "pasa_bajos": {
        "etiqueta": "Pasa-bajos (suavizar)",
        "descripcion": "Atenúa frecuencias altas; suaviza el audio.",
        "tipo": "mono",
        "funcion_nativa": "pasa_bajos",
        "parametro": {
            "nombre": "Suavizado (alpha)",
            "min": 0.01,
            "max": 0.99,
            "default": 0.3,
            "unidad": "",
            "decimales": 2,
        },
    },
    "pasa_altos": {
        "etiqueta": "Pasa-altos (realzar agudos)",
        "descripcion": "Atenúa frecuencias bajas; realza los agudos.",
        "tipo": "mono",
        "funcion_nativa": "pasa_altos",
        "parametro": {
            "nombre": "Suavizado (alpha)",
            "min": 0.01,
            "max": 0.99,
            "default": 0.3,
            "unidad": "",
            "decimales": 2,
        },
    },
    "normalizar": {
        "etiqueta": "Normalización de volumen",
        "descripcion": "Escala el audio para que su pico llegue al nivel objetivo.",
        "tipo": "mono",
        "funcion_nativa": "normalizar",
        "parametro": {
            "nombre": "Nivel objetivo (pico)",
            "min": 0.1,
            "max": 1.0,
            "default": 0.95,
            "unidad": "",
            "decimales": 2,
        },
    },
    "ruido_gate": {
        "etiqueta": "Limpiar ruido de fondo",
        "descripcion": (
            "Baja el volumen en los huecos donde solo hay ruido (silencios entre frases o notas), "
            "con transición suave. No quita ruido mientras suena algo fuerte. "
            "Súbelo hasta que el ruido desaparezca; si se oye cortado, bájalo."
        ),
        "tipo": "mono",
        "funcion_nativa": "ruido_gate",
        "parametro": {
            "nombre": "Umbral",
            "min": -80.0,
            "max": -20.0,
            "default": -55.0,
            "unidad": " dB",
            "decimales": 0,
        },
    },
}


def listar():
    """Devuelve [(id, etiqueta), ...] en un orden estable para la interfaz."""
    return [(id_filtro, info["etiqueta"]) for id_filtro, info in FILTROS.items()]


def obtener(id_filtro):
    return FILTROS[id_filtro]
