#include "../filtro.h"
#include <cmath>

// Ecualizador de 3 bandas: graves, medios y agudos.
//
// Cada banda es un filtro "biquad" (fórmulas de Robert Bristow-Johnson,
// el estándar de los ecualizadores de audio). El parámetro es la ganancia
// en dB: positivo realza la banda, negativo la atenúa, 0 la deja igual.
//   - graves: estante bajo en 150 Hz (bombo, bajo)
//   - medios: campana en 1 kHz (voces, guitarras)
//   - agudos: estante alto en 5 kHz (platillos, brillo)
// Supone 44100 Hz, que es a lo que la app convierte todo el audio.

namespace {

const double FS = 44100.0;
const double PI = 3.14159265358979323846;

enum Tipo { ESTANTE_BAJO, CAMPANA, ESTANTE_ALTO };

void biquad(const float* entrada, float* salida, int cantidad, Tipo tipo, double f0, double q, double db) {
    if (cantidad <= 0) return;
    if (std::fabs(db) < 1e-3) {  // 0 dB: el filtro no cambia nada
        for (int i = 0; i < cantidad; i++) salida[i] = entrada[i];
        return;
    }
    const double A = std::pow(10.0, db / 40.0);
    const double w0 = 2.0 * PI * f0 / FS;
    const double c = std::cos(w0), s = std::sin(w0);
    const double alpha = s / (2.0 * q);
    const double raiz = 2.0 * std::sqrt(A) * alpha;

    double b0, b1, b2, a0, a1, a2;
    if (tipo == ESTANTE_BAJO) {
        b0 = A * ((A + 1) - (A - 1) * c + raiz);
        b1 = 2 * A * ((A - 1) - (A + 1) * c);
        b2 = A * ((A + 1) - (A - 1) * c - raiz);
        a0 = (A + 1) + (A - 1) * c + raiz;
        a1 = -2 * ((A - 1) + (A + 1) * c);
        a2 = (A + 1) + (A - 1) * c - raiz;
    } else if (tipo == ESTANTE_ALTO) {
        b0 = A * ((A + 1) + (A - 1) * c + raiz);
        b1 = -2 * A * ((A - 1) + (A + 1) * c);
        b2 = A * ((A + 1) + (A - 1) * c - raiz);
        a0 = (A + 1) - (A - 1) * c + raiz;
        a1 = 2 * ((A - 1) - (A + 1) * c);
        a2 = (A + 1) - (A - 1) * c - raiz;
    } else {
        b0 = 1 + alpha * A;
        b1 = -2 * c;
        b2 = 1 - alpha * A;
        a0 = 1 + alpha / A;
        a1 = -2 * c;
        a2 = 1 - alpha / A;
    }
    b0 /= a0; b1 /= a0; b2 /= a0; a1 /= a0; a2 /= a0;

    double x1 = 0, x2 = 0, y1 = 0, y2 = 0;  // en double: estable aunque el audio sea largo
    for (int i = 0; i < cantidad; i++) {
        const double x = entrada[i];
        const double y = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2;
        x2 = x1; x1 = x;
        y2 = y1; y1 = y;
        salida[i] = (float)y;
    }
}

}  // namespace

void graves(float* entrada, float* salida, int cantidad, float db) {
    biquad(entrada, salida, cantidad, ESTANTE_BAJO, 150.0, 0.707, db);
}

void medios(float* entrada, float* salida, int cantidad, float db) {
    biquad(entrada, salida, cantidad, CAMPANA, 1000.0, 1.0, db);
}

void agudos(float* entrada, float* salida, int cantidad, float db) {
    biquad(entrada, salida, cantidad, ESTANTE_ALTO, 5000.0, 0.707, db);
}
