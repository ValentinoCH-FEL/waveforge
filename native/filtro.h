#ifndef FILTRO_H
#define FILTRO_H

// En Windows hay que marcar explícitamente qué funciones salen del .dll
// (export). En Mac/Linux no hace falta, el .dylib/.so las expone solas.
// Esta macro resuelve esa diferencia sin tener que tocar el resto del código.
#ifdef _WIN32
  #define FILTRO_API extern "C" __declspec(dllexport)
#else
  #define FILTRO_API extern "C" __attribute__((visibility("default")))
#endif

// Contrato común: todas las funciones de filtro reciben el buffer de
// entrada, un buffer de salida ya reservado (mismo tamaño), la cantidad
// de muestras, y un parámetro propio del filtro.
// Mientras una función nueva respete esta firma, Python la puede llamar
// sin que motor.py ni filtros_registry.py cambien su lógica interna.

FILTRO_API void pasa_bajos(float* entrada, float* salida, int cantidad, float alpha);
FILTRO_API void pasa_altos(float* entrada, float* salida, int cantidad, float alpha);
FILTRO_API void normalizar(float* entrada, float* salida, int cantidad, float nivel_objetivo);

// Silencia muestras por debajo de un umbral de amplitud (limpia ruido
// de fondo de bajo nivel / "sonidos externos" leves).
FILTRO_API void ruido_gate(float* entrada, float* salida, int cantidad, float umbral);

#endif
