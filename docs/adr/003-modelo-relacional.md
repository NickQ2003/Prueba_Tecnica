# ADR-003: modelo relacional con discriminador

- **Estado:** aceptada.
- **Contexto:** individual y colectiva comparten la mayor parte de la identidad de póliza.
- **Decisión:** una entidad `Policy` con `policy_type`, riesgos vinculados y periodos históricos con valores por riesgo.
- **Alternativa descartada:** tablas separadas por tipo o herencia ORM antes de tener diferencias estructurales confirmadas.
- **Consecuencia:** checks y casos de uso validan las diferencias de negocio; canon y primas se conservan en `PeriodRiskValue`.
