# ADR-006: renovación e IPC

- **Estado:** aceptada como regla de demo.
- **Contexto:** canon debe actualizarse con IPC, manteniendo precisión y repetibilidad.
- **Decisión:** `Decimal`/`NUMERIC`, porcentaje fraccional, snapshot de IPC y fórmula versionada por periodo. Duración se conserva en meses calendario y los periodos históricos no se sobrescriben.
- **Alternativa descartada:** `float`, días fijos por mes, tasa cero por defecto o recalcular el retry con un IPC distinto.
- **Consecuencia:** el dato ausente deja la operación en `WAITING_INPUT`; tasas semilla son ficticias. No se implementa prorrateo.
