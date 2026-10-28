# ADR-004: autoridad contractual del CORE

- **Estado:** aceptada como supuesto de demo.
- **Contexto:** no hay contrato ni capacidades confirmadas para el CORE/WebLogic real.
- **Decisión:** el CORE simulado confirma el resultado contractual; la aplicación local conserva solicitudes, estado operativo y auditoría. Timeout significa resultado incierto hasta consultar por la misma identidad.
- **Alternativa descartada:** tratar timeout como rechazo o inventar protocolo/capacidades de WebLogic.
- **Consecuencia:** el mock prueba escenarios reproducibles, pero sus garantías de idempotencia y consulta no se atribuyen al CORE real.
