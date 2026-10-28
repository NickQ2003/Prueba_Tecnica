# ADR-005: Outbox e idempotencia

- **Estado:** aceptada.
- **Contexto:** la aceptación de una edición debe sobrevivir reinicios y los mensajes pueden duplicarse.
- **Decisión:** Operation y Outbox se guardan en una transacción; relay con lease y confirmación de publicación; consumidores reclaman operaciones y deduplican por clave/hash.
- **Alternativa descartada:** publicar a RabbitMQ dentro de una transacción de negocio o confiar en un UUID nuevo por intento.
- **Consecuencia:** se admite entrega al menos una vez y posible duplicado; no se promete exactamente una vez. Una operación incierta se reconcilia, no se reenvía ciegamente.
