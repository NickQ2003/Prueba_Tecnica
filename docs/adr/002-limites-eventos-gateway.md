# ADR-002: límites, eventos y gateway

- **Estado:** aceptada.
- **Contexto:** HTTP no debe acoplar reglas del dominio a FastAPI/Celery y el entorno necesita un punto de entrada ligero.
- **Decisión:** puertos concretos para CORE/notificación, Outbox para eventos durables y Nginx como proxy mínimo delante de API.
- **Alternativa descartada:** eventos de dominio genéricos, bus propio, service mesh o gateway con políticas complejas.
- **Consecuencia:** las operaciones se aceptan durablemente antes del trabajo externo; Nginx aporta enrutamiento/proxy, no HA ni seguridad perimetral avanzada.
