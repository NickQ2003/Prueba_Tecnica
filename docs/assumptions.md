# Supuestos y límites

No se encontró en el repositorio un enunciado específico del Módulo 2. En consecuencia, los puntos siguientes son decisiones de demo y no reglas confirmadas por un CORE o por un enunciado externo.

## Reglas adoptadas

- Moneda `COP`; dinero en `NUMERIC(14,2)` y porcentajes IPC en `NUMERIC(8,5)`. Los porcentajes se almacenan como fracción (`0.08` = 8 %) y dinero se redondea a dos decimales con `ROUND_HALF_UP`.
- El periodo tiene inicio inclusivo y fin exclusivo. La renovación conserva el número de meses calendario y limita al último día del mes destino cuando el día original no existe.
- Duración inicial permitida por la API: de 1 a 36 meses. Los periodos futuros quedan `SCHEDULED` y no se activan antes de su fecha.
- Una póliza individual asocia el tomador con el arrendatario/tenant y tiene un riesgo. Una póliza colectiva tiene un tomador organizacional (supuesto) y suma cánones por riesgo (supuesto).
- El IPC se selecciona por referencia explícita o mediante el índice ficticio más reciente aplicable. Si falta, la renovación queda `WAITING_INPUT`; no se calcula con tasa cero.
- Alta de riesgo en la demo solamente en la fecha de inicio de un periodo para no inventar prorrateo. La cancelación no genera devolución ni recalcula una prima a mitad del periodo; esa regla queda pendiente.
- El CORE es autoritativo para el cambio contractual confirmado. La aplicación es autoritativa para la solicitud, su seguimiento y auditoría.
- En timeout se consulta el CORE con la misma identidad. Si no hay resultado verificable, `NEEDS_REVIEW`; no se reenvía ciegamente.
- Notificación se representa mediante una entrega persistida `SIMULATED`, no correo ni SMS.

## Implementado / simulado / pendiente

- **Implementado:** API, validación, reglas de riesgo, cálculos decimales, operaciones/Outbox, persistencia SQL, reserva por póliza, estados, auditoría, Celery, notificación simulada, migraciones y Docker Compose.
- **Simulado:** contrato HTTP del CORE, consulta por clave idempotente, rechazos, caída, lentitud, respuesta perdida tras aplicar y discrepancia de valor. El mock SQLite persiste aparte del esquema de negocio.
- **Pendiente:** contrato real de WebLogic, su autenticación/protocolo, si ofrece consulta o idempotencia, equivalencia de campos, reglas de fechas/IPC confirmadas, estrategia de compensación, proveedor y garantías de entrega de notificación, identidad corporativa y controles de producción.

La demo no afirma compatibilidad con SOAP ni con Oracle WebLogic. Los tokens estáticos y contraseñas de Compose son únicamente para entorno local. Para producción se requiere diseño operativo, secretos y límites globales de concurrencia; Compose local no proporciona alta disponibilidad.

La revisión `001_create_policy_domain.py` se puede degradar en una base descartable; su `downgrade` elimina las tablas de dominio y sus datos. No degradar sobre datos que deban conservarse.
