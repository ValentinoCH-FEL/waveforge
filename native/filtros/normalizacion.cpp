#include "../filtro.h"
#include <cmath>

// Normalización de pico: encuentra la muestra más fuerte del audio y
// escala todo el arreglo para que ese pico llegue exactamente a
// nivel_objetivo (ej. 0.9 = 90% del máximo posible, dejando margen
// para evitar recorte/clipping).
void normalizar(float* entrada, float* salida, int cantidad, float nivel_objetivo) {
    if (cantidad <= 0) return;

    float pico = 0.0f;
    for (int i = 0; i < cantidad; i++) {
        float valor_absoluto = std::fabs(entrada[i]);
        if (valor_absoluto > pico) pico = valor_absoluto;
    }

    if (pico == 0.0f) {
        for (int i = 0; i < cantidad; i++) salida[i] = 0.0f;
        return;
    }

    float factor = nivel_objetivo / pico;
    for (int i = 0; i < cantidad; i++) {
        salida[i] = entrada[i] * factor;
    }
}
