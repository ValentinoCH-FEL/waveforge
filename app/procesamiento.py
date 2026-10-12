"""
procesamiento.py — aplicar un filtro y mezclar pistas, sin nada de Tkinter.

El panel interactivo (vista previa en vivo) y la exportación final
usan estas mismas funciones, así que lo que escuchas al mover un control
es exactamente lo que vas a obtener en el archivo exportado.
"""

import numpy as np


def aplicar_filtro(motor, info_filtro, parametro, izquierdo, derecho):
    """Aplica el filtro descrito por info_filtro (de filtros_registry)
    sobre izquierdo/derecho usando motor (motor.MotorAudio).

    Devuelve (resultado_izquierdo, resultado_derecho_o_None).
    """
    if info_filtro["tipo"] == "ninguno":
        return izquierdo, derecho

    res_izq = motor.aplicar_mono(info_filtro["funcion_nativa"], izquierdo, parametro)
    res_der = None
    if derecho is not None:
        res_der = motor.aplicar_mono(info_filtro["funcion_nativa"], derecho, parametro)

    return res_izq, res_der


def mezclar(pistas, ganancias):
    """Suma varias pistas, cada una con su ganancia.

    pistas: lista de (izquierdo, derecho) float32 del mismo largo.
    ganancias: lista de números (0 = silenciada, 1 = volumen original).
    Devuelve (izquierdo, derecho) con el resultado recortado a [-1, 1].
    """
    n = len(pistas[0][0])
    salida_izq = np.zeros(n, dtype=np.float32)
    salida_der = np.zeros(n, dtype=np.float32)
    for (izq, der), ganancia in zip(pistas, ganancias):
        if ganancia:
            g = np.float32(ganancia)
            salida_izq += izq * g
            salida_der += der * g
    np.clip(salida_izq, -1.0, 1.0, out=salida_izq)
    np.clip(salida_der, -1.0, 1.0, out=salida_der)
    return salida_izq, salida_der
