#include "../filtro.h"
#include <cmath>

// Noise gate con compuerta suave.
//
// La versión anterior ponía en cero cada muestra que quedaba bajo el umbral;
// eso corta la onda en pedazos y suena áspero. Esta versión mide el nivel
// (envolvente) y abre/cierra la compuerta de forma gradual:
//   - se abre rápido (~3 ms) cuando el nivel supera el umbral,
//   - se cierra despacio (~150 ms) y solo tras mantenerse bajo 80 ms,
//   - no cae a silencio total: deja un piso de ~-26 dB para que no suene "cortado".
//
// umbral_db: nivel en dB (ej. -55) por debajo del cual se considera "solo ruido".
// Supone 44100 Hz, que es a lo que la app convierte todo el audio.
void ruido_gate(float* entrada, float* salida, int cantidad, float umbral_db) {
    const float fs = 44100.0f;
    const float umbral_abre = std::pow(10.0f, umbral_db / 20.0f);
    const float umbral_cierra = umbral_abre * 0.7f;                     // histéresis: evita que "tiemble"
    const float piso = 0.05f;                                           // reducción máxima (~-26 dB)
    const float caida_nivel = std::exp(-1.0f / (0.010f * fs));          // el medidor de nivel baja en ~10 ms
    const float k_apertura = 1.0f - std::exp(-1.0f / (0.003f * fs));
    const float k_cierre = 1.0f - std::exp(-1.0f / (0.150f * fs));
    const int mantener = (int)(0.080f * fs);

    float nivel = 0.0f;
    float ganancia = 1.0f;
    int espera = 0;
    bool abierto = true;

    for (int i = 0; i < cantidad; i++) {
        float x = entrada[i];
        float a = std::fabs(x);
        nivel = (a > nivel) ? a : nivel * caida_nivel;

        if (nivel >= umbral_abre) {
            abierto = true;
            espera = mantener;
        } else if (abierto && nivel < umbral_cierra) {
            if (espera > 0) espera--;
            else abierto = false;
        }

        float objetivo = abierto ? 1.0f : piso;
        float k = (objetivo > ganancia) ? k_apertura : k_cierre;
        ganancia += k * (objetivo - ganancia);
        salida[i] = x * ganancia;
    }
}
