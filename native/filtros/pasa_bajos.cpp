#include "../filtro.h"

// Pasa-bajos exponencial de primer orden.
// Cada muestra de salida se "acerca" a la entrada actual en una
// proporción alpha (0 < alpha < 1). Un alpha chico corta más agudos
// (suaviza mucho), un alpha cercano a 1 casi no filtra.
void pasa_bajos(float* entrada, float* salida, int cantidad, float alpha) {
    if (cantidad <= 0) return;

    salida[0] = entrada[0];
    for (int i = 1; i < cantidad; i++) {
        salida[i] = salida[i - 1] + alpha * (entrada[i] - salida[i - 1]);
    }
}
