# ADR-008: saga diferida por falta de compensaciones

- **Estado:** aceptada.
- **Contexto:** un cambio CORE puede confirmarse aunque la respuesta se pierda, y no hay acciones compensatorias confirmadas.
- **Decisión:** no introducir saga ni rollback remoto ficticio. Se usa identidad estable, consulta y estado `NEEDS_REVIEW` ante incertidumbre o discrepancia.
- **Alternativa descartada:** compensar automáticamente, volver a enviar con nueva identidad o asegurar consistencia exactamente una vez.
- **Consecuencia:** algunos casos requieren intervención reviewer. Una futura saga necesita contrato real y compensaciones aprobadas.
