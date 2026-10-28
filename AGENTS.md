# Instrucciones del repositorio

## Objetivo y alcance

Demo backend para gestión de pólizas de arrendamiento individuales y colectivas. El alcance es API, persistencia, operaciones duraderas, renovación con IPC, integración simulada con CORE, notificación/auditoría, Docker, pruebas y documentación. No añadir frontend, pagos, siniestros, facturación, multiempresa o autenticación IAM completa sin requerimiento nuevo.

## Arquitectura

- Monolito modular FastAPI; dominio y cálculos sin dependencia de HTTP o Celery.
- `app/domain`: reglas y errores de negocio.
- `app/application`: casos de uso y límites transaccionales.
- `app/adapters`: PostgreSQL, CORE HTTP y notificaciones.
- `app/entrypoints`: API, workers, scheduler y relay.
- CORE y notificaciones son fronteras externas con puertos/adaptadores; no agregar repositorios genéricos, workflows genéricos, CQRS, event sourcing o microservicios.
- Toda edición contractual pasa por operación persistida + Outbox + adaptador CORE. No mantener transacciones abiertas durante llamadas HTTP.

## Reglas de negocio que no deben romperse

- `policy_type` es `INDIVIDUAL` o `COLLECTIVE`; el tomador vive en Policy y el asegurado/beneficiario en Risk.
- Una individual confirmada tiene exactamente un riesgo activo. Una colectiva requiere uno o más; no cancelar el último riesgo.
- Cancelar es cambio de estado, no borrado físico. No reactivar riesgos cancelados ni cambiar arbitrariamente el tipo de una póliza emitida.
- Rechazar renovación de póliza cancelada, duplicación de periodos y modificación paralela incompatible por póliza.
- Contrato confirmado y estado operativo pendiente son conceptos distintos. No presentar una aceptación `202` como confirmación contractual.
- IPC y dinero usan Decimal/NUMERIC. `0.08` representa 8 %. No tomar IPC cero por defecto ni recalcular reintentos con otro snapshot.
- Periodos conservan su historia. El CORE real no está especificado; no inventar capacidades ni presentar el mock como WebLogic.
- Colectiva: el tomador organizacional, canon por riesgo y agregación son supuestos, no requisitos confirmados.
- Usar `Decimal` y `NUMERIC`; conservar snapshots de canon/IPC/prima por periodo. Una tasa ausente no es cero.
- Periodo con inicio inclusivo, fin exclusivo y duración en meses calendario. No inventar prorrateos.
- No sostener una transacción ni lock de PostgreSQL mientras se llama al CORE.
- Un timeout del CORE es incierto; consultar con la misma operación y dejar `NEEDS_REVIEW` cuando no sea verificable.

## Servicios y operaciones Docker

- Servicios: `api`, `db`, `rabbitmq`, `worker`, `scheduler`, `outbox-relay`, `migrate`, `core-mock`, `gateway` y cliente `curl`; perfiles `demo`, `tools` y `test`.
- PostgreSQL y RabbitMQ no publican puertos al host; el mock CORE solo se resuelve dentro de Compose.
- API y procesos de fondo esperan a `migrate` exitoso; las migraciones corren una vez como proceso finito.
- Scheduler permanece separado y único; no escalarlo junto con la API.
- Healthchecks requieren timeouts. No usar pausas arbitrarias (`sleep`) para esperar disponibilidad.
- `MAX_CORE_CONCURRENCY` es por worker; el total crece con las réplicas.
- No usar resultados de Celery para representar estado de negocio; `Operation` en PostgreSQL es la autoridad.

## Comandos Docker

Preparar y arrancar local:

```bash
cp .env.example .env
docker compose up --build -d
docker compose --profile demo run --rm seed
```

Pruebas aisladas (incluyen Ruff, validación de migración, drift y E2E con RabbitMQ/worker):

```bash
docker compose -p policies-test -f compose.yaml -f compose.test.yaml \
  --profile test up --build --abort-on-container-exit --exit-code-from tests tests
```

Lint independiente:

```bash
docker compose --profile tools run --rm tools ruff check .
docker compose --profile tools run --rm tools ruff format --check .
```

No declarar que estos comandos pasan sin ejecutarlos en el estado actual del cambio.

## Migraciones

- Alembic es la autoridad de esquema; revisiones `001_nombre_snake_case.py`, `002_...` y cadena lineal única.
- Generar con `python -m scripts.create_migration nombre_snake_case [--autogenerate]` dentro del servicio Docker `tools`, con el repo montado.
- Inspeccionar siempre operaciones generadas; incluir downgrade viable y señalar pérdida destructiva.
- Cada cambio de esquema va en una nueva migración. Prohibido editar/renumerar revisiones aplicadas o sustituir Alembic por `create_all` o SQL ordenado a mano.
- Validar con `python scripts/check_migrations.py` y `alembic check`.
- Resolver colisiones de numeración entre ramas; el helper no coordina clones ni equipos.

## Código, pruebas y documentación

- Identificadores y nombres de código en inglés; documentación en español. Comentarios explican decisiones, no repiten el código.
- Preferir funciones explícitas y dependencias justificadas. No añadir librerías o abstracciones sin razón concreta.
- Cada cambio funcional requiere pruebas que cubran comportamiento y límites, no solo reflejar la implementación.
- Persistencia, restricciones y concurrencia se prueban con PostgreSQL, no sustituyéndolo por SQLite. SQLite solo corresponde al estado aislado del simulador CORE.
- Tests de persistencia usan el perfil Compose con base y datos aislados; al limpiar, no tocar volúmenes del entorno local.
- Cubrir idempotencia, duplicados, concurrencia, timeouts inciertos y límites de negocio cuando cambien esos flujos.
- Definir payloads Pydantic explícitos; PATCH no debe cambiar estado contractual, prima confirmada ni datos internos.
- Los endpoints contractuales requieren `Idempotency-Key`, Operation+Outbox atómicos y `Location` para consultar el resultado.
- Cambios incompatibles del contrato requieren nueva versión; no crear `/v2` sin necesidad.
- No usar BackgroundTasks para trabajo durable. No prometer entrega/ejecución exactamente una vez.
- Actualizar README, supuestos o arquitectura cuando cambie el comportamiento observable.
- Documentar qué está implementado, qué está simulado y qué requiere el contrato CORE real.
- `requirements-dev.txt` reutiliza `requirements.txt`; `pyproject.toml` configura herramientas y no mantiene otra lista de dependencias.
- Evitar logs con nombres, documentos, direcciones o secretos; transportar correlation/operation IDs.

## Seguridad y operaciones

- Nunca versionar `.env`, tokens reales, datos personales reales o credenciales. `.env.example` solo contiene valores sintéticos.
- Tokens incluidos son exclusivamente para demo. Core mock no se publica fuera de la red Compose.
- Los tokens estáticos no constituyen IAM de producción. No agregar secretos al contexto o imagen Docker.
- Mantener CORS deshabilitado salvo que un consumidor lo justifique; limitar tamaño de payload y paginación.
- No publicar al remoto, borrar volúmenes/datos ni ejecutar acciones destructivas sin autorización explícita.
- No aumentar workers automáticamente: `MAX_CORE_CONCURRENCY` es por worker, no un límite global distribuido.
- El scheduler se ejecuta como proceso separado; no escalarlo junto con API.
- Compose local no implica HA ni disponibilidad 24/7.

## Commits

En esta tarea el usuario autorizó commits incrementales con identificadores `PT-001`, `PT-002` en orden ascendente. Usar formato `feat(PT-001): concise lowercase english description` (tipos permitidos `feat`, `fix`, `refactor`, `style`). Nunca hacer push.
