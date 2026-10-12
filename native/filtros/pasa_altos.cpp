#include "../filtro.h"

// Pasa-altos simple: se obtiene restándole a la señal original su propia
// versión pasa-bajos. Deja pasar los cambios bruscos (agudos) y atenúa
// lo que varía lento (graves).
void pasa_altos(float* entrada, float* salida, int cantidad, float alpha) {
    if (cantidad <= 0) return;

    float anterior_entrada = entrada[0];
    float anterior_salida = 0.0f;
    salida[0] = 0.0f;

    for (int i = 1; i < cantidad; i++) {
        salida[i] = alpha * (anterior_salida + entrada[i] - anterior_entrada);
        anterior_entrada = entrada[i];
        anterior_salida = salida[i];
    }
}
