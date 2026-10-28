# Demo de pólizas de arrendamiento

Interfaz HTML5 sencilla conectada a una API FastAPI para pólizas individuales y colectivas. Las ediciones contractuales se procesan como operaciones duraderas y pasan por un CORE HTTP simulado. Docker Compose contiene la UI, API, PostgreSQL, RabbitMQ, workers y un cliente cURL: no hay que instalar Python, una base de datos, el broker ni cURL en el dispositivo.

## En otro dispositivo: instalación desde cero

Requisitos: Docker Desktop con Docker Compose v2, Git y una terminal Bash (en Windows, Bash de WSL2). El repositorio remoto debe contener estos commits antes de clonarlo; esta tarea no publicó ni hizo `push`.

```bash
git clone https://github.com/NickQ2003/Prueba_Tecnica.git
cd Prueba_Tecnica
cp .env.example .env
```

Arranca todos los servicios y espera a sus healthchecks:

```bash
docker compose up --build -d
docker compose ps
```

La interfaz está disponible en <http://localhost:8080>; Swagger en <http://localhost:8080/docs>. PostgreSQL, RabbitMQ y el CORE mock permanecen dentro de la red de Docker. El mock CORE no se publica al host.

Los valores de `.env.example` son ficticios para demo. Para cambiar contraseñas, actualiza tanto la variable de contraseña como su URL de conexión. No compartas ni versionees `.env`.

## Usar la interfaz

Abre <http://localhost:8080>. La pantalla permite crear Parties y pólizas, buscar pólizas, ver riesgos y periodos, añadir o cancelar riesgos, registrar IPC, solicitar renovaciones y consultar/reconciliar operaciones. El botón **Token API** permite cambiar entre los tokens `read`, `operator` y `reviewer` definidos en `.env`.

Una modificación contractual muestra primero `202 · PENDING/PROCESSING`; la interfaz la identifica como solicitud aceptada, no como contrato confirmado. El estado se actualiza desde la operación persistida en PostgreSQL. Si una renovación espera IPC, el panel de operaciones permite seleccionar una tasa y reanudarla con un token reviewer.

La UI y la API comparten origen (`localhost:8080`); no se instala framework ni se habilita CORS.

## Crear o actualizar la mock data

En Bash, desde la raíz del repositorio y con Compose arriba, carga las variables y ejecuta el script idempotente:

```bash
set -a
. ./.env
set +a
eval "$(docker compose --profile demo run --rm -T seed python -m scripts.mock_data --shell)"
export MOCK_API_URL=http://gateway:8080
```

El script crea Parties sintéticas, una póliza individual, una colectiva con dos riesgos, una tasa IPC ficticia de `0.08000` (8 %) y una operación en `NEEDS_REVIEW`. Espera a que worker y relay procesen las operaciones. No resetea ni borra datos existentes; las IDs quedan cargadas como variables `MOCK_*` en esa terminal.

Para imprimir solo el JSON de IDs, sin cargar variables Bash:

```bash
docker compose --profile demo run --rm seed
```

| Fixture | Datos sintéticos |
|---|---|
| Individual | Arrendatario Demo; canon mensual `1500000.00` COP; un riesgo. |
| Colectiva | Administradora Demo; dos riesgos con cánones `1700000.00` y `2100000.00` COP. |
| IPC | `DEMO-2026`, tasa `0.08000`, marcada ficticia. |
| Reconciliación | Una solicitud con CORE no disponible que termina en `NEEDS_REVIEW`. |

Ejemplo para ver IDs:

```bash
printf '%s\n' "$MOCK_POLICY_INDIVIDUAL_ID" "$MOCK_POLICY_COLLECTIVE_ID" "$MOCK_IPC_RATE_ID" "$MOCK_OPERATION_NEEDS_REVIEW_ID"
```

Todas las llamadas cURL de abajo se ejecutan dentro del contenedor de herramientas, no requieren cURL en el host:

```bash
dcurl() {
  docker run --rm --network "${COMPOSE_PROJECT_NAME:-arrenda-demo}_default" \
    curlimages/curl:8.12.1 "$@"
}
```

Usa `-i` para ver el código HTTP y las cabeceras. Los tokens de demo son `API_READ_TOKEN`, `API_OPERATOR_TOKEN` y `API_REVIEWER_TOKEN`; el `.env.example` trae valores listos para desarrollo.

## cURL de todos los endpoints

### Health, documentación y métricas

```bash
dcurl -i "$MOCK_API_URL/health/live"
```

Esperado: `200`, `{"status":"live"}`.

```bash
dcurl -i "$MOCK_API_URL/health/ready"
```

Esperado: `200`, `{"status":"ready","database":"ready"}`.

```bash
dcurl -i "$MOCK_API_URL/openapi.json"
dcurl -i "$MOCK_API_URL/metrics"
```

Esperado: `200`; OpenAPI devuelve el contrato y `/metrics` métricas Prometheus agregadas.

### Parties y pólizas

Crear una Party ficticia (`POST /api/v1/parties`):

```bash
DOC_RUN="$(date +%s)"
dcurl -i -X POST "$MOCK_API_URL/api/v1/parties" \
  -H "Authorization: Bearer $API_OPERATOR_TOKEN" \
  -H 'Content-Type: application/json' \
  --data-raw "{\"party_type\":\"PERSON\",\"display_name\":\"Persona cURL Demo\",\"document_reference\":\"CURL-PERSON-$DOC_RUN\"}"
```

Esperado: `201 Created`, con el `id` de la Party.

Listar o buscar Parties (`GET /api/v1/parties`), usado por la interfaz para poblar los selectores:

```bash
dcurl -i "$MOCK_API_URL/api/v1/parties?limit=20&offset=0&search=Demo" \
  -H "Authorization: Bearer $API_READ_TOKEN"
```

Esperado: `200 OK`, respuesta paginada con `items`, `limit`, `offset` y `total`.

Crear una póliza individual con una solicitud contractual (`POST /api/v1/policies`):

```bash
DOC_POLICY_KEY="curl-policy-$DOC_RUN"
dcurl -i -X POST "$MOCK_API_URL/api/v1/policies" \
  -H "Authorization: Bearer $API_OPERATOR_TOKEN" \
  -H "Idempotency-Key: $DOC_POLICY_KEY" \
  -H 'Content-Type: application/json' \
  --data-raw "{\"policy_type\":\"INDIVIDUAL\",\"holder_party_id\":\"$MOCK_PARTY_TENANT_ID\",\"starts_on\":\"$(date -u +%F)\",\"duration_months\":12,\"risks\":[{\"property_address\":\"Dirección cURL ficticia\",\"tenant_party_id\":\"$MOCK_PARTY_TENANT_ID\",\"landlord_party_id\":\"$MOCK_PARTY_LANDLORD_ID\",\"monthly_rent\":\"1250000.00\"}]}"
```

Esperado: `202 Accepted`, con `operation_id` y `Location`. La operación puede estar `PENDING` o `PROCESSING`; copia su ID y consulta el endpoint de operaciones hasta que llegue a estado terminal:

```bash
DOC_CREATE_OPERATION_ID='<operation_id de la respuesta anterior>'
dcurl -i "$MOCK_API_URL/api/v1/operations/$DOC_CREATE_OPERATION_ID" \
  -H "Authorization: Bearer $API_READ_TOKEN"
```

Esperado final: `200 OK`, `status: SUCCEEDED` y `result.policy_id`. Un `202` confirma persistencia de la solicitud, no el contrato.

Listar con paginación y filtro (`GET /api/v1/policies`):

```bash
dcurl -i "$MOCK_API_URL/api/v1/policies?limit=20&offset=0&policy_type=COLLECTIVE" \
  -H "Authorization: Bearer $API_READ_TOKEN"
```

Esperado: `200 OK`, con `items`, `limit`, `offset` y `total`.

Consultar una póliza y sus riesgos (`GET /api/v1/policies/{policy_id}`):

```bash
dcurl -i "$MOCK_API_URL/api/v1/policies/$MOCK_POLICY_INDIVIDUAL_ID" \
  -H "Authorization: Bearer $API_READ_TOKEN"
```

Esperado: `200 OK`, póliza con un riesgo activo.

Actualizar notas no contractuales (`PATCH /api/v1/policies/{policy_id}`):

```bash
dcurl -i -X PATCH "$MOCK_API_URL/api/v1/policies/$MOCK_POLICY_INDIVIDUAL_ID" \
  -H "Authorization: Bearer $API_OPERATOR_TOKEN" \
  -H 'Content-Type: application/json' \
  --data-raw '{"notes":"Actualizada desde cURL"}'
```

Esperado: `200 OK`; los demás campos internos y contractuales no se pueden cambiar con PATCH.

### Riesgos y cancelación

Listar riesgos (`GET /api/v1/policies/{policy_id}/risks`):

```bash
dcurl -i "$MOCK_API_URL/api/v1/policies/$MOCK_POLICY_COLLECTIVE_ID/risks" \
  -H "Authorization: Bearer $API_READ_TOKEN"
```

Esperado: `200 OK`, dos riesgos `ACTIVE`.

Añadir un tercer riesgo colectivo (`POST /api/v1/policies/{policy_id}/risks`). La mock data comienza su periodo hoy; esta alta demo no aplica prorrateo:

```bash
dcurl -i -X POST "$MOCK_API_URL/api/v1/policies/$MOCK_POLICY_COLLECTIVE_ID/risks" \
  -H "Authorization: Bearer $API_OPERATOR_TOKEN" \
  -H 'Idempotency-Key: curl-add-risk-001' \
  -H 'Content-Type: application/json' \
  --data-raw "{\"property_address\":\"Apartamento cURL ficticio\",\"tenant_party_id\":\"$MOCK_PARTY_TENANT_2_ID\",\"landlord_party_id\":\"$MOCK_PARTY_LANDLORD_2_ID\",\"monthly_rent\":\"900000.00\"}"
```

Esperado: `202 Accepted`; consulta `Location` hasta `SUCCEEDED` y copia `result.risk_id` si necesitas operar el nuevo riesgo. En pólizas individuales no se permite añadir otro riesgo.

Cancelar un riesgo colectivo (`POST /api/v1/policies/{policy_id}/risks/{risk_id}/cancellations`):

```bash
dcurl -i -X POST "$MOCK_API_URL/api/v1/policies/$MOCK_POLICY_COLLECTIVE_ID/risks/$MOCK_RISK_COLLECTIVE_1_ID/cancellations" \
  -H "Authorization: Bearer $API_OPERATOR_TOKEN" \
  -H 'Idempotency-Key: curl-cancel-risk-001'
```

Esperado: `202 Accepted`, seguido de operación `SUCCEEDED`; el riesgo queda `CANCELLED`, no se borra.

### IPC, renovación, periodos y operaciones

Crear una tasa ficticia (`POST /api/v1/ipc-rates`):

```bash
DOC_RATE_PERIOD="CURL-$(date +%y%m%d%H%M%S)"
dcurl -i -X POST "$MOCK_API_URL/api/v1/ipc-rates" \
  -H "Authorization: Bearer $API_REVIEWER_TOKEN" \
  -H 'Content-Type: application/json' \
  --data-raw "{\"reference_period\":\"$DOC_RATE_PERIOD\",\"effective_on\":\"$(date -u +%F)\",\"rate\":\"0.07500\",\"source\":\"IPC ficticio cURL\",\"is_fictional\":true}"
```

Esperado: `201 Created` con el ID de la tasa.

Listar IPC (`GET /api/v1/ipc-rates`):

```bash
dcurl -i "$MOCK_API_URL/api/v1/ipc-rates" \
  -H "Authorization: Bearer $API_READ_TOKEN"
```

Esperado: `200 OK`; la tasa semilla es ficticia y `0.08` representa 8 %.

Solicitar renovación sin IPC (`POST /api/v1/policies/{policy_id}/renewals`):

```bash
dcurl -i -X POST "$MOCK_API_URL/api/v1/policies/$MOCK_POLICY_INDIVIDUAL_ID/renewals" \
  -H "Authorization: Bearer $API_OPERATOR_TOKEN" \
  -H 'Idempotency-Key: curl-renewal-001' \
  -H 'Content-Type: application/json' \
  --data-raw '{"ipc_rate_id":null}'
```

Esperado: `202 Accepted`, estado `WAITING_INPUT`; no se usa IPC cero.

Reanudar con IPC (`POST /api/v1/operations/{operation_id}/resume`):

```bash
RENEWAL_OPERATION_ID='<operation_id de la renovación anterior>'
dcurl -i -X POST "$MOCK_API_URL/api/v1/operations/$RENEWAL_OPERATION_ID/resume" \
  -H "Authorization: Bearer $API_REVIEWER_TOKEN" \
  -H 'Content-Type: application/json' \
  --data-raw "{\"ipc_rate_id\":\"$MOCK_IPC_RATE_ID\"}"
```

Esperado: `200 OK`, operación `PENDING`; luego consulta `GET /api/v1/operations/{operation_id}` hasta `SUCCEEDED`.

Consultar periodos (`GET /api/v1/policies/{policy_id}/periods`):

```bash
dcurl -i "$MOCK_API_URL/api/v1/policies/$MOCK_POLICY_INDIVIDUAL_ID/periods" \
  -H "Authorization: Bearer $API_READ_TOKEN"
```

Esperado tras renovar: `200 OK`, con dos periodos históricos.

Consultar cualquier operación (`GET /api/v1/operations/{operation_id}`):

```bash
dcurl -i "$MOCK_API_URL/api/v1/operations/$MOCK_OPERATION_CREATE_INDIVIDUAL_ID" \
  -H "Authorization: Bearer $API_READ_TOKEN"
```

Esperado: `200 OK` con tipo, estado, intentos y resultado si ya fue confirmado.

Reconciliar la operación preparada en `NEEDS_REVIEW` (`POST /api/v1/operations/{operation_id}/reconcile`):

```bash
dcurl -i -X POST "$MOCK_API_URL/api/v1/operations/$MOCK_OPERATION_NEEDS_REVIEW_ID/reconcile" \
  -H "Authorization: Bearer $API_REVIEWER_TOKEN"
```

Esperado: `200 OK`, estado `NEEDS_REVIEW` mientras CORE no tenga resultado. La reconciliación consulta con la misma identidad y no reenvía la mutación.

### Fallos reproducibles

**401**, token ausente. Esperado: `401 Unauthorized`, `{"detail":"Bearer token required"}`.

```bash
dcurl -i "$MOCK_API_URL/api/v1/policies"
```

**403**, token de lectura intentando crear Party. Esperado: `403 Forbidden`, `{"detail":"Insufficient permission"}`.

```bash
dcurl -i -X POST "$MOCK_API_URL/api/v1/parties" \
  -H "Authorization: Bearer $API_READ_TOKEN" \
  -H 'Content-Type: application/json' \
  --data-raw '{"party_type":"PERSON","display_name":"Sin permiso","document_reference":"CURL-FORBIDDEN-001"}'
```

**404**, UUID válido que no existe. Esperado: `404 Not Found`, `{"detail":"policy not found"}`.

```bash
dcurl -i "$MOCK_API_URL/api/v1/policies/00000000-0000-4000-8000-000000000000" \
  -H "Authorization: Bearer $API_READ_TOKEN"
```

**409**, clave idempotente ya usada con el cuerpo cambiado: repite la creación individual anterior con el mismo `DOC_POLICY_KEY` y cambia el canon a `1250001.00`. Esperado: `{"detail":"Idempotency-Key was already used with a different request"}`.

```bash
dcurl -i -X POST "$MOCK_API_URL/api/v1/policies" \
  -H "Authorization: Bearer $API_OPERATOR_TOKEN" \
  -H "Idempotency-Key: $DOC_POLICY_KEY" \
  -H 'Content-Type: application/json' \
  --data-raw "{\"policy_type\":\"INDIVIDUAL\",\"holder_party_id\":\"$MOCK_PARTY_TENANT_ID\",\"starts_on\":\"$(date -u +%F)\",\"duration_months\":12,\"risks\":[{\"property_address\":\"Dirección cURL ficticia\",\"tenant_party_id\":\"$MOCK_PARTY_TENANT_ID\",\"landlord_party_id\":\"$MOCK_PARTY_LANDLORD_ID\",\"monthly_rent\":\"1250001.00\"}]}"
```

**409**, cancelación del único riesgo individual. Esperado: `{"detail":"the last active risk cannot be cancelled independently"}`.

```bash
dcurl -i -X POST "$MOCK_API_URL/api/v1/policies/$MOCK_POLICY_INDIVIDUAL_ID/risks/$MOCK_RISK_INDIVIDUAL_ID/cancellations" \
  -H "Authorization: Bearer $API_OPERATOR_TOKEN" \
  -H 'Idempotency-Key: curl-cancel-last-risk-001'
```

**422**, póliza individual con dos riesgos: el validador lo rechaza antes de consultar Parties. Esperado: JSON de validación con `status: 422` y detalle de que la póliza individual debe empezar con exactamente un riesgo.

```bash
dcurl -i -X POST "$MOCK_API_URL/api/v1/policies" \
  -H "Authorization: Bearer $API_OPERATOR_TOKEN" \
  -H 'Idempotency-Key: curl-invalid-individual-001' \
  -H 'Content-Type: application/json' \
  --data-raw "{\"policy_type\":\"INDIVIDUAL\",\"holder_party_id\":\"$MOCK_PARTY_TENANT_ID\",\"starts_on\":\"$(date -u +%F)\",\"duration_months\":12,\"risks\":[{\"property_address\":\"A\",\"tenant_party_id\":\"$MOCK_PARTY_TENANT_ID\",\"landlord_party_id\":\"$MOCK_PARTY_LANDLORD_ID\",\"monthly_rent\":\"100.00\"},{\"property_address\":\"B\",\"tenant_party_id\":\"$MOCK_PARTY_TENANT_ID\",\"landlord_party_id\":\"$MOCK_PARTY_LANDLORD_ID\",\"monthly_rent\":\"100.00\"}]}"
```

**Rechazo del CORE**: la API acepta durablemente con `202`; consulta la operación y verifica `REJECTED`.

```bash
REJECT_KEY="curl-core-reject-$(date +%s)"
dcurl -i -X POST "$MOCK_API_URL/api/v1/policies" \
  -H "Authorization: Bearer $API_OPERATOR_TOKEN" \
  -H "Idempotency-Key: $REJECT_KEY" \
  -H 'X-Demo-Core-Scenario: reject' \
  -H 'Content-Type: application/json' \
  --data-raw "{\"policy_type\":\"INDIVIDUAL\",\"holder_party_id\":\"$MOCK_PARTY_TENANT_ID\",\"starts_on\":\"$(date -u +%F)\",\"duration_months\":12,\"risks\":[{\"property_address\":\"Dirección rechazada ficticia\",\"tenant_party_id\":\"$MOCK_PARTY_TENANT_ID\",\"landlord_party_id\":\"$MOCK_PARTY_LANDLORD_ID\",\"monthly_rent\":\"1000000.00\"}]}"
```

**Timeout tras aplicar**: la API acepta la operación y el worker reconcilia con la misma identidad; esperado final `SUCCEEDED` sin efecto duplicado. Los escenarios CORE solo están habilitados para demo con `DEMO_CONTROLS_ENABLED=true`.

```bash
TIMEOUT_KEY="curl-timeout-$(date +%s)"
dcurl -i -X POST "$MOCK_API_URL/api/v1/policies" \
  -H "Authorization: Bearer $API_OPERATOR_TOKEN" \
  -H "Idempotency-Key: $TIMEOUT_KEY" \
  -H 'X-Demo-Core-Scenario: timeout_after_apply' \
  -H 'Content-Type: application/json' \
  --data-raw "{\"policy_type\":\"INDIVIDUAL\",\"holder_party_id\":\"$MOCK_PARTY_TENANT_ID\",\"starts_on\":\"$(date -u +%F)\",\"duration_months\":12,\"risks\":[{\"property_address\":\"Dirección timeout ficticia\",\"tenant_party_id\":\"$MOCK_PARTY_TENANT_ID\",\"landlord_party_id\":\"$MOCK_PARTY_LANDLORD_ID\",\"monthly_rent\":\"1000000.00\"}]}"
```

Consulta el `operation_id` devuelto por `202` con `GET /api/v1/operations/{operation_id}`; debe terminar en `SUCCEEDED`.

## Volúmenes, pruebas y limpieza

`docker compose down` detiene la demo y conserva PostgreSQL, RabbitMQ y CORE mock. No uses `down -v` si quieres conservar esos datos.

La suite aislada usa PostgreSQL, RabbitMQ, API, worker y CORE mock:

```bash
docker compose -p policies-test -f compose.yaml -f compose.test.yaml \
  --profile test up --build --abort-on-container-exit --exit-code-from tests tests
```

El contenedor `tests` ejecuta Ruff, formato, validación Alembic, `alembic check` y pytest, incluido un flujo API–Outbox–RabbitMQ–worker–CORE. Cada operación devuelve `202` para indicar aceptación durable; el estado contractual se confirma consultando `/api/v1/operations/{operation_id}`.
