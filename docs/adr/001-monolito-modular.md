# ADR-001: monolito modular

- **Estado:** aceptada.
- **Contexto:** el alcance agrupa pólizas, riesgos y renovaciones y no requiere despliegues independientes.
- **Decisión:** un servicio FastAPI con módulos lógicos de dominio, aplicación, adaptadores y entrypoints.
- **Alternativa descartada:** microservicios y framework propio de workflows.
- **Consecuencia:** una transacción PostgreSQL puede proteger invariantes de una póliza; los límites se mantienen por dependencias y contratos de puerto, sin costo operativo distribuido.
