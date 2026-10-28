# Gestión de pólizas de arrendamiento

Demo de un monolito modular para pólizas individuales y colectivas. Las modificaciones contractuales se aceptan como operaciones persistentes y se confirman de forma asíncrona a través de un adaptador HTTP y un CORE simulado.

## Alcance y estado

- **Implementado:** API versionada, pólizas/riesgos/periodos, IPC decimal, idempotencia, reservas por póliza, PostgreSQL/Alembic, Outbox, RabbitMQ/Celery, reconciliación, auditoría y notificación simulada.
- **Simulado:** CORE HTTP, respuestas de negocio y entrega de notificaciones. El CORE mock persiste sus resultados en un volumen SQLite separado.
- **Pendiente de contrato real:** WebLogic, su protocolo, capacidades reales de consulta/idempotencia y cualquier proveedor externo de correo/SMS.

No hay un enunciado específico del Módulo 2 en el repositorio. Los supuestos adoptados están en [`docs/assumptions.md`](docs/assumptions.md). La demo local con Docker Compose no ofrece alta disponibilidad ni es una garantía de disponibilidad 24/7.

## Requisitos locales

Solo Docker con Docker Compose v2. No hace falta instalar Python, PostgreSQL, RabbitMQ ni las dependencias de la aplicación en el host.

```bash
cp .env.example .env
docker compose up --build -d
docker compose ps
```

Compose levanta gateway Nginx (`localhost:8080`), API, PostgreSQL, RabbitMQ, worker, scheduler, relay Outbox y CORE mock. La API y los procesos de trabajo esperan a que Alembic termine correctamente. PostgreSQL y RabbitMQ no publican puertos en el host; el CORE mock solo está disponible dentro de la red Compose.

Swagger: <http://localhost:8080/docs>

OpenAPI: <http://localhost:8080/openapi.json>

Health: <http://localhost:8080/health/live> y <http://localhost:8080/health/ready>

Los tokens de `.env.example` y las credenciales locales son ficticios para demo. Cámbialos antes de exponer el gateway a otra red. No copies secretos a la imagen.
Si cambias `POSTGRES_PASSWORD` o `RABBITMQ_PASSWORD`, actualiza también su URL de conexión correspondiente en `.env`.

## Datos sintéticos y demo

El seed es idempotente: crea Parties y un IPC ficticio, y encola las pólizas individual y colectiva mediante el caso de uso y el Outbox.

```bash
docker compose --profile demo run --rm seed
```

Consulta el estado de las operaciones indicadas por el seed con `GET /api/v1/operations/{operation_id}`. Para recorrer una creación y una renovación cuyo CORE aplica pero cuya respuesta se pierde por timeout:

```bash
docker compose exec api python -m scripts.demo_flow
```

El flujo usa `timeout_after_apply`; consulta el CORE con la misma identidad y no repite la renovación. El resultado esperado imprime ambos estados `SUCCEEDED` y dos periodos. El escenario solo está disponible si `DEMO_CONTROLS_ENABLED=true`.

## API y autenticación demo

Todos los endpoints de negocio requieren `Authorization: Bearer <token>`:

- Lectura: `API_READ_TOKEN` (`demo-read-token` en la demo).
- Operación: `API_OPERATOR_TOKEN` (`demo-operator-token`).
- Revisión: `API_REVIEWER_TOKEN` (`demo-reviewer-token`).

Ejemplo de alta de Party:

```bash
curl -i http://localhost:8080/api/v1/parties \
  -H 'Authorization: Bearer demo-operator-token' \
  -H 'Content-Type: application/json' \
  -d '{"party_type":"PERSON","display_name":"Arrendatario Demo","document_reference":"DEMO-LOCAL-001"}'
```

Las pólizas, riesgos, cancelaciones y renovaciones requieren `Idempotency-Key` y devuelven `202 Accepted` con `Location: /api/v1/operations/{id}`. Ese estado confirma que la solicitud quedó persistida, no que el CORE ya haya aplicado el cambio.

Rutas principales: `POST /api/v1/policies`, `GET /api/v1/policies`, `GET /api/v1/policies/{id}`, `PATCH /api/v1/policies/{id}` (solo `notes`), `POST /api/v1/policies/{id}/risks`, cancelación de riesgo, renovación, periodos y estado de operación. Los cambios contractuales deben pasar por el adaptador del CORE.

Si no se proporciona IPC, una renovación queda en `WAITING_INPUT`. Un usuario reviewer registra IPC con `POST /api/v1/ipc-rates` y reanuda la operación con `POST /api/v1/operations/{id}/resume`. El IPC semilla de 8 % es ficticio.

## Migraciones

Las migraciones son la autoridad del esquema. La revisión existente es `001_create_policy_domain.py`; no se debe editar después de aplicar. Para una revisión nueva, con la base local levantada y migrada:

```bash
docker compose --profile tools run --rm --user "$(id -u):$(id -g)" tools \
  python -m scripts.create_migration add_policy_attribute --autogenerate
docker compose --profile tools run --rm --user "$(id -u):$(id -g)" tools \
  python scripts/check_migrations.py
docker compose --profile tools run --rm --user "$(id -u):$(id -g)" tools alembic check
```

El helper comprueba una única cadena lineal y asigna el siguiente identificador numérico. Revisa a mano el `upgrade`/`downgrade` autogenerado. Dos ramas de trabajo aún pueden producir el mismo siguiente ID; resuelve esa colisión antes de integrarlas.

## Tests, lint y migraciones en Docker

El perfil de test usa una base PostgreSQL, RabbitMQ y CORE mock con volúmenes aislados del perfil local; incluye API, worker y relay reales para el caso end-to-end.

```bash
docker compose -p policies-test -f compose.yaml -f compose.test.yaml \
  --profile test up --build --abort-on-container-exit --exit-code-from tests tests
```

El contenedor `tests` ejecuta Ruff, formato, validación de la cadena Alembic, `alembic check` y pytest. Para detener el stack de pruebas sin borrar sus datos:

```bash
docker compose -p policies-test -f compose.yaml -f compose.test.yaml --profile test down
```

Para borrar exclusivamente los volúmenes del proyecto aislado de pruebas, y solo cuando ya no necesites sus datos:

```bash
docker compose -p policies-test -f compose.yaml -f compose.test.yaml --profile test down -v
```

Lint manual dentro de Docker:

```bash
docker compose --profile tools run --rm tools ruff check .
docker compose --profile tools run --rm tools ruff format --check .
```

## Operación y limpieza

```bash
docker compose logs -f api worker outbox-relay
docker compose down
```

`down` conserva los volúmenes de PostgreSQL, RabbitMQ y CORE mock. No uses `down -v` en el perfil local salvo que quieras borrar esos datos explícitamente.

## Arquitectura y limitaciones

- [Arquitectura, transiciones y diagramas](docs/architecture.md)
- [Supuestos de negocio y limitaciones](docs/assumptions.md)
- [Decisiones de arquitectura](docs/adr/)
- [Instrucciones para agentes y contribución](AGENTS.md)

El número de workers es por proceso. El presupuesto agregado al CORE es aproximadamente `réplicas de worker × MAX_CORE_CONCURRENCY`; no hay un límite distribuido global. El scheduler es un proceso separado y debe mantenerse como una sola instancia en Compose.
