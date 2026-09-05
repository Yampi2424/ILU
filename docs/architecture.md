# Arquitectura de I.L.U.

## Objetivo

I.L.U. será un asistente inteligente diseñado con una arquitectura orientada a la nube.

## Principios

- El computador local será solamente un cliente.
- El procesamiento principal estará en la nube.
- El código estará versionado mediante Git.
- La memoria estará separada del código.
- Los modelos de IA serán intercambiables.
- Las credenciales y secretos nunca se almacenarán en Git.
- La arquitectura deberá poder migrarse entre proveedores cloud.

## Componentes

### App
Punto de entrada de la aplicación.

### Core
Lógica principal y coordinación de I.L.U.

### Memory
Sistema de memoria persistente.

### Models
Integración con modelos de inteligencia artificial.

### Tools
Herramientas que I.L.U. podrá utilizar.

### Config
Configuraciones no sensibles.

### Tests
Pruebas automáticas.

### Docs
Documentación del proyecto.

## Bloque 3 — Autorización y auditoría (2026-09)

Puntal de la transformación a entidad agéntica: separar PENSAR / PROPONER /
EJECUTAR y que ninguna respuesta del LLM equivalga a permiso de ejecución.

- `app/security.py` · `SecurityGate` — compuerta de autorización
  (fail-closed). Permisos de herramienta: `safe` / `ask` / `blocked`.
  Niveles de autonomía vía `ILU_AUTONOMY` (`manual` / `assisted` /
  `autonomous`, default `assisted`).
- `app/audit.py` · `AuditLog` — historial JSONL por acción
  (`memory/audit.jsonl`, gitignored, `ILU_AUDIT_PATH` para cambiar ruta).
  Nunca registra argumentos ni secretos; campos con nombre sensible se
  enmascaran.
- `app/core.py` — toda ejecución de herramienta pasa por
  `_execute_tool_call(mode=...)`: `direct` (determinista) o `model`
  (propuesta por el LLM), consulta `SecurityGate` y audita el intento y
  el resultado. En `manual`, el modelo no ejecuta herramientas por su
  cuenta. `ask` siempre requiere autorización humana.

Decisiones de seguridad aplicadas:
- Cierre a prueba de fallos: permiso desconocido → denegar.
- `safe` auto-ejecuta (compatibilidad con el comportamiento actual).
- `ask` se detiene en la compuerta en todos los niveles: no existe aún un
  registro de autorizaciones previas, por lo que `autonomous` no
  auto-aprueba nada adicional (PLANIFICADO).

Pendientes previstos (PLANIFICADO): registro de autorizaciones previas y
cadena de autoridad familiar, auditoría consultable vía `/ask`, autoevaluación,
tareas en segundo plano, subagentes.

---

## Bloque 4 — Tareas en segundo plano y concurrencia (2026-09)

### Implementado
- `tasks/manager.py` · `TaskManager` — almacén persistente de tareas:
  estados `created|queued|running|paused|completed|failed|cancelled`,
  progreso 0-100, resultado, error, reintentos (`retries`/`max_retries`),
  prioridad y timestamps (`created/updated/started/completed`). Persistencia
  JSON en `memory/tasks.json` (gitignored, `ILU_TASKS_PATH` para cambiarla,
  `ILU_TASK_MAX_RETRIES` para reintentos por defecto, default 3; best-effort:
  un fallo de disco no rompe al sistema). Métodos: `create`, `get`,
  `list_tasks(state=)`, `set_state`, `set_progress`, `set_result`,
  `set_error`, `record_retry`, `stats`.
- `tasks/__init__.py` — exporta `TaskManager`.
- `app/__main__.py` — `ThreadingHTTPServer`: I.L.U. atiende varias
  peticiones a la vez, condición para tareas largas en segundo plano
  mientras conversa.
- Ejecutor `_run_task(task_id, callable_fn)` con **reintentos reales**:
  toma `created→running`, ejecuta el callable; en éxito → `set_result`
  (completed, progress 100); en error → `record_retry`; si agota
  `max_retries` → `set_error` (failed). Auditoría del resultado
  (`task_result`, success True/False).
- `register_background_task(key, fn)` — catálogo de trabajos que I.L.U.
  puede lanzar en segundo plano via `POST /tasks` con `"callable": "<key>"`.
- Integración con el orquestador: `core.py` incorpora `_task_command(message)`
  como etapa 2.5 del pipeline. Comandos naturales:
  - Crear: "crea una tarea: ..." → `task_create`; "crea una tarea" (sin
    título) → `task_create_pending`.
  - Listar: "qué tareas", "mis tareas", "listar tareas" → `task_list`.
  - Estado: "cómo va la tarea", "progreso de la tarea" → `task_status`.
  Comandos auditados (`task_create`).
- Rutas HTTP:
  - `GET /tasks` — lista tareas, soporta filtro `?state=running`.
  - `GET /tasks/{id}` — estado completo de una tarea.
  - `POST /tasks` — crea una tarea `{title, description, priority,
    max_retries?, callable?}`. Audita `task_create`.
  - `PUT /tasks/{id}/state|progress|result` — actualización.
- `GET /about` incluye `"tasks": task_manager.stats()` (total y conteos
  por estado).
- Instancia única compartida: `task_manager = core.tasks` (core y HTTP
  comparten el mismo almacén).

### Compatibilidad preservada
- `/healthz`, `/about`, `/` y `POST /ask` intactos (mismo contrato JSON).
- `GET /tasks` y `POST /tasks` no colisionan con rutas existentes.

### Decisiones tomadas
- ThreadingHTTPServer en lugar de un framework async: mínimo, sin
  dependencias nuevas, suficiente para tareas I/O-bound.
- Una sola instancia de `TaskManager` compartida entre core y HTTP para
  evitar drift de estado entre ambos.
- Reintentos en el ejecutor de tareas (no solo en el registro): un fallo
  transitorio se reintenta automáticamente antes de marcar como fallida.

### Pendiente (PLANIFICADO)
- Subagentes: cada subagente actualiza una tarea del `TaskManager`.
- Tareas periódicas programadas (cron-like).

---

## Bloque 5 — Memoria como arquitectura de inteligencia (2026-09)

La memoria de I.L.U. se diseña no como un chatbot con memoria agregada, sino
como parte de la arquitectura de la inteligencia: base para aprendizaje,
planificación, herramientas, subagentes y continuidad. El criterio de
aceptación: almacenar, recuperar, relacionar, actualizar y consultar
distintos tipos de memoria **sin quedar atada al JSON** — y preparados para
múltiples almacenes y futura sincronización entre ubicaciones.

### Arquitectura implementada

- `memory/types.py` — taxonomía ampliada a la arquitectura final:
  `conversation`, `episodic`, `semantic`, `personal`, `family`, `working`,
  `procedural`, `knowledge`, `experience`, `skill`, `task`, `error`. Cada
  tipo declara un eje de **ciclo de vida** (`volatile` / `temporal` /
  `permanent`) que alimenta la retención. Se conservan los tipos legados
  (`general`, `preference`, `project`, `fact`).
- `memory/backends.py` — capa de almacenes intercambiables:
  - `MemoryRecord` ampliado: **`version` + `revisions`** (historico
    versionado), **`source`** (procedencia) y **`device_id`** (ubicación),
    **`tombstone`** (borrado lógico para sync);
  - `JsonBackend` (default, local, sin Internet) con **cache en memoria +
    escritura atómica** (un `.tmp` renombrado), sin releer el archivo por
    operación; lee el formato legado y el ampliado;
  - `PostgresBackend` (tabla `ilu_memory_v2`, columnas ampliadas con
    **migración idempotente** `ADD COLUMN IF NOT EXISTS`), listo para Neon
    pero sin acoplarse a él: sirve cualquier PostgreSQL vía `DATABASE_URL`;
  - `InMemoryBackend` (tests / modos sin disco);
  - `create_backend()` — factory por `ILU_MEMORY_BACKEND` (`json|postgres`).
- `memory/graph.py` — relaciones tipadas entre recuerdos dentro del record
  (simétricas, funcionan en cualquier backend).
- `memory/router.py` · `MemoryRouter` — **fachada única** del sistema a la
  memoria:
  - **Versionado real**: `update`/`correct` conservan el valor anterior en
    `revisions` (con versión, marca de tiempo y motivo) e incrementan
    `version`; corregir información **no destruye el historial**.
  - **Claves cortas únicas** (`mem_` + 8 hex) en vez de `memory_N`
    secuenciales: condición para sincronizar entre ubicaciones sin
    colisiones. Se sigue leyendo el formato legado.
  - **`source` / `device_id`**: cada recuerdo registra su procedencia y qué
    ubicación de I.L.U. lo tocó (default `ILU_DEVICE_ID`, `"local"`).
  - **Ciclo de vida básico**: la memoria `working` (volatile) se limpia al
    arrancar (`prune_volatile`); `prune(days=)` poda temporales antiguas
    sin tocar lo permanente.
  - **Consulta dirigida**: `recall_intent` / `recall_context` /
    `recall_recent` como puerta única para core, razonamiento y futuros
    subagentes.
  - **Costura de sincronización**: `changes_since(cursor)` y
    `apply_changes(batch)` (merge por clave+versión) quedan listos, pero el
    **SyncEngine real es PLANIFICADO** (no hay transporte ni reloj
    vectorial).
  - Sigue exponiendo la interfaz del antiguo `MemoryStore`
    (`save`/`search`/`load_all`) para el pipeline sin ruptura.

### Compatibilidad preservada

- Todo el pipeline (`/ask`, `/healthz`, `/about`, tareas) intacto.
- `MemoryStore` y el formato de datos antiguo se leen sin cambios.
- La memoria de trabajo se limpia al arrancar; `working` es nuevo, no había
  datos que perder.
- Acepta keys legadas `memory_N` (core sigue generándolas con
  `_next_memory_key`); las keys nuevas son `mem_*`.

### Decisiones tomadas

- **Local-first**: cada ubicación persiste su memoria localmente y luego
  sincronizará; la "nube" no es obligatoria. Satisface a la vez "funciona
  sin Internet" y "misma identidad en varias ubicaciones".
- **Versionado en vez de sobrescritura**: corregir conserva el historial
  (auditable y reversible), base del futuro aprendizaje y auto-corrección.
- **Recency only ranks, no rank for relevance**: la relevancia exige
  evidencia léxica; importancia y recencia solo ordenan candidatos reales.
- **Eje lifecycle declarado en la taxonomía** (volatile/temporal/permanent),
  aplicado hoy a `working` y a la poda; la compresión de conversaciones es
  PLANIFICADO.

### Pendiente (PLANIFICADO)
- Aprendizaje autónomo y auto-modificación (requerirá autorización; el
  versionado y `source="learning"` ya lo habilitan).
- Subagentes escribiendo en la memoria.
- Autoridad familiar (tipos `family`/`personal` ya reservados).
- Búsqueda semántica/vectorial como backend adicional del recall.
- **SyncEngine real** entre ubicaciones (transporte, merge por reloj
  vectorial y resolución de conflictos; la interfaz `changes_since` /
  `apply_changes` ya está).
- Pool de conexiones Postgres por operación.
- Compresión de conversaciones antiguas → `episodic` → `semantic`.

---

## Bloque 6 · Panel de herramientas ejecutables

Estado: **IMPLEMENTADO** (Bl. 6, 2026-09-01).

### Qué se construyó

Panel de 5 herramientas reales, todas pasando por `SecurityGate` (el LLM
**propone**; la compuerta **decide**) y por `AuditLog`:

| Herramienta | Permiso | Qué hace |
|---|---|---|
| `system_time` | `safe` | Fecha/hora del sistema (ya existía) |
| `web_search` | `safe` | Búsqueda web ligera sin clave (DDG Instant Answers) |
| `read_file` | `safe` | Lee un archivo de texto **dentro** del workspace |
| `notify` | `safe` | Deja una notificación local dirigida al usuario (log JSONL) |
| `write_file` | `ask` | Crea/reescribe un archivo — exige autorización humana |

### Confinamiento

- **Workspace**: toda lectura/escritura de archivos se resuelve contra
  `ILU_WORKSPACE` (default: cwd) con `Path.resolve()` + `is_relative_to`.
  Traversal (`../`, rutas absolutas fuera) → `path_outside_workspace`.
- **write_file = ask**: aun en autonomía `autonomous`, sin registro de
  autorización previa la compuerta se detiene (`ask`) y el handler JAMÁS se
  ejecuta. No existe registro de autorizaciones aún (PLANIFICADO).
- **web_search sin red** y **read_file rechazado** fallan de forma
  **explícita** (`web_search_unavailable`, `path_outside_workspace`) — no se
  oculta el fallo.

### Despacho directo (sin LLM)

`_identify_tool` reconoce por lenguaje natural y despacha de forma
determinista las herramientas `safe` (evita coste/latencia del modelo):
- "busca en internet [sobre] X" → `web_search`
- "lee/muéstrame/abre el archivo X" → `read_file`
- "notifícame/avísame/déjame una nota X" → `notify`

Si el argumento obligatorio (query/path/message) falta o está vacío, no se
despacha y se delega al modelo.

### Cambio en ToolManager

`ToolManager.execute` ahora **propaga** un fallo *funcional* que el handler
devuelve como dict con `success: False` (antes lo envolvía como éxito).
Motivo: la búsqueda sin red o una ruta fuera del workspace no son
excepciones ni "éxito"; deben reportarse como fallo. Los fallos por
excepción (`tool_execution_failed`) siguen igual.

### Archivos

- **Nuevos**: `tools/search.py`, `tools/filesystem.py`, `tools/notify.py`
- **Modificados**: `tools/__init__.py` (5 registros + alias `_notify` para
  no sombrear el submódulo), `tools/manager.py` (propagación de fallo),
  `app/core.py` (`_identify_tool` + extracción de argumentos),
  `.gitignore` (`memory/notifications.jsonl`)
- **Tests nuevos**: `tests/test_tools_search.py`,
  `tests/test_tools_filesystem.py`, `tests/test_tools_notify.py`,
  `tests/test_tools_panel.py`

### Verificación

- `py_compile` OK · `git diff --check` OK
- `pytest tests/` → **197 passed**
- Smoke HTTP: `/healthz` OK; `/ask("hola")` → greeting con 5 tools
  expuestas; `/ask("notifícame …")` → `tool_use`/`notify` y escribe
  `memory/notifications.jsonl`.

### Limitaciones / PLANIFICADO

- **Autorizaciones previas recordadas** ✓ (Bloque siguiente): al resolver
  una solicitud, `remember=True` emite un grant DURABLE que SecurityGate
  reutiliza en futuras ejecuciones de la misma capacidad; `indefinite`
  lo hace no-vigente-pero-siempre-revocable. Por lenguaje natural,
  "autoriza siempre X" / "recuerda que puedes X" emiten un permiso
  recordado, y "autoriza X" sigue siendo de UN solo uso (menor privilegio).
- `write_file` solo se prueba con proveedor fake (la ejecución real del
  LLM requiere Ollama/red).
- `web_search` usa solo Instant Answers (1 resultado); un buscador real es
  PLANIFICADO.
- `read_file` acota tamaño (200 KB) y `write_file` no limita aún tamaño.
- Subagentes / sub-assistant anidado → **Bloque 7**.

---

## Bloque 7 · Sub-agentes y sub-assistant anidado

Estado: **IMPLEMENTADO** (Bl. 7, 2026-09-01).

### Qué se construyó

Capacidad de I.L.U. para **delegar** una sub-tarea a un sub-assistant
secuencial (`SubAgent`, `app/subagent.py`) que comparte el **mismo**
proveedor, toolset, `SecurityGate` y `AuditLog` que el flujo principal.
El modelo propone; la compuerta decide — dentro y fuera del sub-agente.

### Reglas de seguridad (heredadas, nunca elevadas)

- El `SubAgent` recibe **las mismas instancias** de `tools`/`security`/
  `audit`/`memory` del padre (verificado por test).
- Las tools propuestas por el sub-agente pasan por `SecurityGate` con
  `mode="model"`: en autonomía `manual`, una tool `safe` propuesta por el
  (sub)modelo se detiene en `ask`, igual que en el flujo principal.
- Una tool `ask` (p. ej. `write_file`) se detiene en la compuerta; el
  handler del sub-agente JAMÁS corre.
- Cada tool del sub-agente se audita (`tool_attempt`/`tool_result`) y el
  propio sub-agente se audita como acción `subagent`.

### Detección flexible (no dependiente de frases exactas)

`_subagent_task(message)` reconoce intención de delegación de dos formas,
siempre exigiendo una **tarea no trivial** (≥ 12 caracteres) tras el
marcador:

1. **Frases explícitas**: "sub-agente", "sub-assistant", "encargá a",
   "manejá esto", "investiga en paralelo", "delega esto"…
2. **Tokens con frontera de palabra**: "delega"/"delegar"/"subagente"…
   (no coincide dentro de "encargado" ni "delegado").

Peticiones normales ("qué hora es", "busca en internet X", "hola") y
verbos sueltos ("delega" sin tarea) NO disparan un sub-agente (testeado).

### Bucle secuencial acotado

Cada ronda consulta al modelo con contexto de trabajo; si propone una
herramienta, se ejecuta vía la compuerta y el resultado se retroalimenta;
termina en texto final, error, o **`max_rounds`** (default 3, configurable)
→ `truncated: true`. Memoria separada: el sub-agente solo *lee* contexto
tipo working/personal vía `MemoryRouter.recall_context` (no escribe en el
hilo principal).

### Archivos

- **Nuevos**: `app/subagent.py`, `tests/test_subagent.py`
- **Modificados**: `app/core.py` (stage 2.75 en `process()` +
  `_subagent_task`/`_is_subagent_request`/`_run_subagent`)
- **Intactos de forma deliberada**: `SecurityGate`, `AuditLog`,
  `ToolManager` y todos los contratos del API (`/ask`, `/healthz`,
  `/about`).

### Verificación

- `py_compile` OK · `git diff --check` OK
- `pytest tests/` → **205 passed** (197 Bloques 1–6 + 8 nuevos del Bl. 7)
- Smoke HTTP: `/healthz` OK; `/ask("hola")` → greeting con 5 tools.

### Limitaciones / PLANIFICADO

- Sub-agente **secuencial**: el paralelismo real de sub-agentes es
  PLANIFICADO.
- La detección exige una tarea descripta (al menos 12 caracteres): "manejá
  esto por favor" sin tarea NO dispara un sub-agente.
- Sin historial multi-turno persistido para el sub-agente (contexto fresco
  por delegación, propósito).
- Sin límite de rondas LLM dentro de una ronda de herramienta (un solo
  tool_call por ronda).

## Bloque 8 · Autoridad, permisos y autonomía gobernada

**Principio rector: *la inteligencia propone, la Autoridad decide, y solo
se ejecuta con autoridad.*** La respuesta de un LLM NUNCA equivale a
permiso de ejecución; toda herramienta pasa por `SecurityGate`, y nada de
eso puede concederse a sí mismo.

### Qué se construyó

Capas del sistema de autoridad (`security/`), de abajo a arriba:

- **`PrincipalRegistry`** — quién es el OWNER (autoridad raíz, de
  `ILU_OWNER_ID`). Solo `owner`/`family_root` son `ROOT_TYPES`.
- **`Policy`** — reglas separadas del código en `security/policy.json`
  (acciones prohibidas, sensibilidad, duración por defecto).
- **`GrantStore`** — autorizaciones explícitas, auditable, JSONL
  gitignored. Default **no permanente** (single_action = 1 uso).
- **`DeviceRegistry`** — dispositivos autorizados con challenge-response
  **HMAC-SHA256** (stdlib; ruta de ascenso a asimétrico documentada).
- **`EmergencyRegistry`** — solo protocolos **predefinidos** en policy,
  que la raíz activa/desactiva.
- **`SpoofingGuard`** — contador de fallos de verificación en ventana;
  marca sospecha al superar el umbral (capa de señal, audita incidentes).
- **`AuthorizationRequestStore`** — solicitudes reversibles; con
  `AuthorizationRequired`, una tarea se PAUSA y espera.
- **`Authority`** — **única** capa que concede/revoca permisos, cambia la
  autonomía, registra dispositivos y activa emergencias. JAMÁS se inyecta
  a la inteligencia, tools ni subagentes.
- **`SecurityGate`** — el ÚNICO punto de enforcement, extendido de forma
  retrocompatible: consulta acciones prohibidas, emergencias, grants
  activos y sospecha de spoofing.

### Modo de operación

- `manual` → decide `ask` ante cualquier tool ejecutable (siempre pide).
- `assisted` (default) → la compuerta pide si hace falta; un grant activo
  auto-aprueba.
- `autonomous` → un grant activo auto-aprueba; sin grant sigue pidiendo
  (jamás se autoconcede).

Los grants auto-aprueban SOLO en `assisted`/`autonomous`. En `manual` un
grant no basta: la autonomía manda.

### Subagentes

Heredan el contexto de autorización del padre (consultan el MISMO
`grant_store`; los grants de un solo uso se consumen compartidos) pero
**NUNCA** reciben `Authority` — no pueden concederse, revocar, elevar la
autonomía, registrarse ni activar emergencias (testeado
`test_subagent_authority_isolation.py`).

### Frontend por lenguaje natural y HTTP

- `POST /ask`: "autoriza X", "revoca X", "estado de permisos", "cambia la
  autonomía a asistido/autónomo/manual" (solo root; X prohibido se
  rechaza). El nivel concedido es SIEMPRE `execution`.
- `GET /security`, `GET /grants`, `GET /policy`,
  `GET /authorization-requests`; `POST /grants`,
  `POST /authorization-requests/{id}`, `POST /autonomy`.

### Archivos

- **Nuevos**: `security/` (11 módulos + `policy.json`),
  `tests/test_authority.py`, `test_grant_store.py`, `test_principal.py`,
  `test_policy.py`, `test_emergency.py`, `test_device.py`,
  `test_spoofing.py`, `test_authorization_request.py`,
  `test_security_gate_grants.py`, `test_task_authorization.py`,
  `test_core_authorization_nl.py`,
  `test_subagent_authority_isolation.py`
- **Modificados**: `app/core.py`, `app/__main__.py`, `app/security.py`,
  `app/subagent.py`, `config/settings.py`, `tasks/manager.py`,
  `tests/conftest.py`, `.gitignore`
- **Intactos**: contratos del API (`/ask`, `/healthz`, `/about`).

### Verificación

- `py_compile` OK · `git diff --check` OK
- `pytest tests/` → **333 passed** (incluye los 12 archivos nuevos del
  Bloque 8; sin regresiones sobre Bloques 1–7).
- Smoke HTTP: grant, change de autonomía y rechazo de acción prohibida OK.

### Limitaciones / PLANIFICADO

- HMAC-SHA256 (simétrico) en device auth; ascenso documentado a
  firma asimétrica (Ed25519).
- El spoofing es una capa de señal: la decisión final la toma la compuerta.
- Requests nunca compiladas no se purgan automáticamente (happy path por
  ahora); la política de retención es PLANIFICADO.
- Registro/verificación de dispositivos y protocolos de emergencia
  implementados; la UX web para los mismos es PLANIFICADO.

## Bloque 9 · Tool-calling nativo y fallback cloud→local

### Qué se construyó

- **`app/toolshape.py` (NUEVO)** — traducción de tool-shapes entre
  proveedores. I.L.U. habla UNA forma canónica
  (`[{"name", "description", "permission"}]` para tools;
  `{"tool", "arguments", "reason"}` para tool_calls) y esta capa la
  traduce al wire-format nativo:
  - `openai_functions(tools)` → array `tools` estilo OpenAI function
    (el MISMO esquema que entienden Ollama nativo y OpenAI-compat /
    OmniRoute).
  - `parse_tool_calls(message)` → normaliza `message.tool_calls` de
    AMBAS variantes: Ollama entrega `function.arguments` como **objeto
    parseado (dict)**; OpenAI-compat como **STRING JSON** (a veces
    `"null"` o ausente). El argumento `"id"` (OpenAI) se conserva; el
    `"index"` (Ollama) es opcional y no se exige.
- **Tool calling NATIVO en ambos providers** — `LocalProvider` y
  `OmniRouteProvider` ahora incluyen `tools` en el payload cuando se los
  pasa, y `_decide_result` (base) da prioridad a los tool_calls nativos
  sobre el formato heredado JSON-en-content. Ambos aplican el mismo gateo
  por `available_tools`: una tool nativa no permitida jamás se ejecuta
  (fail-closed), se responde como texto.
- **`FallbackProvider` + `create_runtime_provider()`** — con
  `ILU_AI_PROVIDER=omniroute`, I.L.U. ya no depende de un único punto de
  fallo: si OmniRoute devuelve un error de red/configuración, se delega
  en Ollama local. El resultado anota `fallback: true` y
  `provider_used` para que el orquestador sepa qué motor generó la
  propuesta.
- **Fix real encontrado por los tests**: `_decide_result` trataba
  `content: None` (típico de un tool_call nativo) como la cadena
  `"None"`. Ahora `None` se trata como vacío, de modo que un tool_call
  nativo denegado responde el mensaje fail-closed correcto.

### Archivos

- **Nuevos**: `app/toolshape.py`, `tests/test_toolshape.py`,
  `tests/test_fallback.py`
- **Modificados**: `app/providers.py`, `app/core.py`,
  `tests/test_providers_local.py`, `tests/test_providers_omniroute.py`

### Verificación

- `py_compile` OK · `git diff --check` OK
- `pytest tests/` → **350 passed** (27 nuevos del Bloque 9; cero
  regresiones sobre Bloques 1–8).
- Fallback verificado de punta a punta:
  `test_real_omniroute_error_falls_back_to_local` — OmniRoute devuelve
  HTTP 500 y la respuesta final proviene de Ollama local con
  `fallback: true`.

### Limitaciones / PLANIFICADO

- El fallback SOLO cubre el camino cloud→local. El camino inverso
  (local caído, cloud sano) no está envuelto: con el proveedor por
  defecto `local` no hay respaldo.
- `keep_alive`/`num_predict` son específicos de Ollama; OmniRoute envía
  el payload OpenAI estándar (los parámetros por proveedor se
  mantienen dentro de cada clase).
- El tool-shape soporta actualmente parámetros vacíos (sin JSON-schema)
  en `properties`; el refinado del esquema por tool es PLANIFICADO.

## Bloque 10 · Historial de conversación multi-turn

### Qué se construyó

- **`memory/conversations.py` (NUEVO)** — `ConversationStore`: guarda los
  turnos de cada sesión (`session_id`) para que I.L.U. mantenga contexto
  entre mensajes de un mismo usuario. Persistencia JSONL local
  (`memory/conversations.jsonl`, gitignored) o tabla Postgres
  `ilu_conversations` cuando hay `DATABASE_URL`, reutilizando el patrón de
  `MemoryStore`. API: `append`, `recent(limit)`, `reset`, `list_sessions`
  y `transcript`.
- **Inyección de historial en `core.py`** — `process(message,
  session_id=None)` carga los últimos `ILU_HISTORY_TURNS` turnos de la
  sesión y los inyecta como contexto al modelo en la llamada a `generate`.
  Es **solo contexto de lectura**: no cambia el gateo de herramientas
  (Bloque 9) ni la autoridad (Bloque 8).
- **Registro de turnos** — en el camino del modelo, se guarda el turno del
  usuario antes de la llamada y el del asistente tras la respuesta, de modo
  que la siguiente consulta de la sesión tiene contexto.
- **API HTTP** — `/ask` acepta `session_id` (opcional; por defecto
  `"default"`); `GET /conversations/{session_id}` (auditar/debug) y
  `DELETE /conversations/{session_id}` (resetear la sesión).
- **Config** — `ILU_CONVERSATIONS_PATH` (default
  `memory/conversations.jsonl`) e `ILU_HISTORY_TURNS` (default `6`).

### Archivos

- **Nuevos**: `memory/conversations.py`, `tests/test_conversations.py`,
  `tests/test_core_multi_turn.py`
- **Modificados**: `app/core.py`, `app/__main__.py`,
  `config/settings.py`, `tests/conftest.py`, `.gitignore`
- **Intactos**: `app/providers.py`, `app/toolshape.py`, `security/`,
  `tools/`, contratos HTTP existentes.

### Verificación

- `py_compile` OK · `git diff --check` OK
- `pytest tests/` → **363 passed** (13 nuevos del Bloque 10; cero
  regresiones sobre Bloques 1–9).
- Smoke HTTP: `/healthz` OK; `/ask` con `session_id` registra turnos
  (`user`/`assistant`); `GET /conversations/s1` lista el historial;
  `DELETE` lo resetea.

### Limitaciones / PLANIFICADO

- El historial es solo del camino del modelo; los turnos que responden
  caminos deterministas (saludo, hora, memoria, tareas) no se guardan
  como historial de sesión (aunque sí en la memoria `conversation`).
- Historial en texto plano, sin embeddings; la búsqueda semántica del
  historial es PLANIFICADO.
- Un solo hilo de conversación por sesión (sin ramas); los subagentes
  (Bloque 7) usan su propio historial fresco, no el de la sesión padre.
- Sin frontend/UX web de conversaciones.

## Bloque 11 · JSON-schema por herramienta + validación

### Qué se construyó

- **Esquema declarativo por herramienta** — `ToolManager.register` acepta
  un `schema` (JSON-schema) opcional por tool. `get_schema(name)` lo
  expone y `list_tools_full()` lo incluye (sin alterar `list_tools()`,
  que preserva el contrato público de Bloques 1–10).
- **`openai_functions()` emite `parameters` reales** — si la tool declara
  `schema`, el array `tools` nativo lleva ese JSON-schema; si no, se
  mantiene `properties: {}` (retrocompatible con el Bloque 9).
- **Validación fail-closed** — `validate_arguments(schema, arguments)`
  en `app/toolshape.py`: comprueba `required` y tipos
  (string/boolean/integer/number). Se invoca en `_execute_tool_call`
  ANTES de la compuerta y de ejecutar: unos argumentos inválidos se
  rechazan de forma honesta sin tocar el handler; una tool sin esquema
  siempre pasa.
- **Esquemas del panel** — `system_time`, `web_search` (query required),
  `read_file` (path required), `notify` (message required) y `write_file`
  (path+content required) declaran su esquema.

### Archivos

- **Nuevos**: `tests/test_tool_schema.py`, `tests/test_tool_validation.py`
- **Modificados**: `app/toolshape.py`, `app/core.py`, `tools/manager.py`,
  `tools/__init__.py`, `docs/architecture.md`
- **Intactos**: `app/providers.py`, `app/__main__.py`, `security/`
  (SecurityGate/Authority/AuditLog), `memory/`, contratos HTTP.

### Verificación

- `py_compile` OK · `git diff --check` OK
- `pytest tests/` → **382 passed** (19 nuevos del Bloque 11; cero
  regresiones sobre Bloques 1–10).
- Retrocompatibilidad verificada: `list_tools()` público sin cambios;
  tool sin esquema emite `properties: {}` y se ejecuta sin bloqueo.

### Limitaciones / PLANIFICADO

- Validación de tipos básicos (string/integer/number/boolean); sin
  `enum`, `pattern`, `minimum` ni anidación profunda (PLANIFICADO).
- La validación es sintáctica; la semántica de los valores la decide la
  tool al ejecutar.
- El esquema se emite igual para Ollama y OmniRoute (mismo wire-format).

## Bloque 13 · Ejecución real gateada (run_command / open_app / media_control)

### Qué se construyó

- **Tres integraciones reales sobre el mundo** — hasta aquí, "actuar" era
  solo escribir archivos dentro del workspace. Ahora I.L.U. puede ejecutar
  comandos (de una lista blanca), abrir aplicaciones y controlar multimedia,
  SIEMPRE a través del mismo camino gateado (SecurityGate + grant + auditoría).
- **Dos diales independientes, ambos fail-closed**:
  1. **Grant para la capacidad** (`run_command`, `open_app`, `media_control`):
     emitido por Authority/owner. Sin grant → `authorization=ask` + solicitud
     abierta.
  2. **Lista blanca** (`security/run_commands.json`): qué comandos exactos,
     qué apps, qué acciones de media, y los confinamientos (timeout, max
     output, metachars vetados). El grant no otorga poder sobre lo que no
     está en la lista. `shell` crudo SIGUE prohibido en policy.json.
- **`CommandPolicy`** — carga la política desde disco (commiteado); un
  archivo ausente/corrupto deja la lista vacía (fail-closed). `shlex.split` +
  primer token en allowlist + sin metachars en ningún token; ejecución
  SIEMPRE `shell=False`. Overrides de confinamientos por env
  (`ILU_WORLD_TIMEOUT` / `ILU_WORLD_MAX_OUTPUT`).
- **`pre_authorized=True`** — el core despacha las 3 tools con
  `permission="ask"`; sus handlers delegan en la integración con
  `pre_authorized=True` para no consumir dos veces un grant de un solo uso.
- **Despacho por lenguaje natural** (determinista, antes de mirar al LLM):
  "ejecutá/ejecuta/corré/corre &lt;comando&gt;", "abrí/abre &lt;app&gt;"
  (NUNCA "abre el archivo…", que sigue siendo `read_file`), "pausá la
  música", "siguiente canción", "subí/bajá el volumen", "silenciá".
- **Rechazos honestos y legibles** — fuera de la lista blanca, metachars,
  app no instalada o backend ausente producen mensajes claros (nunca un
  fallo mudo ni una ejecución simulada).

### Archivos

- **Nuevos**: `security/command_policy.py`, `security/run_commands.json`,
  `tests/test_command_policy.py`, `tests/test_integrations_world.py`,
  `tests/test_core_world_nl.py`
- **Modificados**: `security/policy.json` y `security/policy.py`
  (sensitivity de las 3 capacidades; `prohibited` intacto),
  `config/settings.py` (env-config), `app/integrations.py`
  (`execute(pre_authorized)` + 3 ejecutores), `app/core.py`
  (`_register_world_tools`, despacho NL, respuesta humana),
  `tests/test_tools_panel.py` (8 tools expuestas por ILUCore)
- **Intactos**: `security/securitygate.py`, `security/authority.py`,
  `tools/manager.py` (reutilizados tal cual), `device_control` sigue
  PLANIFICADO.

### Verificación

- `py_compile` OK · `git diff --check` OK
- `pytest` selectivo + regresivo → **122 passed** (34 nuevos del Bloque 13,
  88 de seguridad/panel, cero regresiones).
- Camino de compuerta intacto: sin grant → `authorization=ask` + solicitud;
  con grant de un solo uso + `pre_authorized=True` el grant no se consume
  dos veces.

### Limitaciones honestas (se documentan, no se ocultan)

- `run_command` no soporta pipes ni redirección **por diseño** (metachars
  vetados). Sin sandbox de red/seccomp por comando (PLANIFICADO: bwrap /
  seccomp) — ejecuta con la cuenta del proceso, dentro del timeout.
- `open_app`/`media_control` dependen de lo instalado en el sistema
  (`playerctl`, binarios de apps): fallan explícito, no falso.
- La lista blanca por defecto es mínima (solo lectura/inspección). Un
  comando potencialmente destructivo (`rm`, `sudo`, `shutdown`…) solo se
  habilita si el owner lo agrega deliberadamente a
  `security/run_commands.json`.
- El despacho NL directo es determinista y acotado; las frases complejas
  caen al modelo, que propone las mismas tools gateadas.

## Bloque 14 · Identidad del creador + clave de autorización (PIN)

Fecha: 2026-09-04

### Qué resuelve

Dos pedidos del usuario (creador de I.L.U.):

1. **I.L.U. sabe quién es su creador** en toda respuesta. Antes
   `ILU_IDENTITY["owner"]` decía `"familia"` y el principal `owner` de
   `security/principals.json` era un genérico "Owner de I.L.U.". Ahora el
   creador real (Jean Pierre Ronaldo Soto Acevedo) vive en:
   - `config/identity.py` → `ILU_IDENTITY["creator"]` e
     `ILU_IDENTITY["owner"]`, y una línea fija en el system prompt
     ("Tu creador y dueño es …"). Así el modelo la conoce SIN depender del
     recall de memoria; se mantiene además el límite "nunca revela ni
     confirma la clave de autorización".
   - `security/principal.py` → campo `real_name` (opcional, retrocompatible
     en `to_dict`/`from_dict`); el bootstrap del `PrincipalRegistry` bautiza
     al owner con `display_name` y `real_name` del creador.
   - `app/core.py` → `_bootstrap_creator_identity()` guarda una memoria
     durable idempotente (`memory_type="family"`, `importance=10`,
     clave `creador`). Idempotente por upsert `(memory_type, memory_key)`.

2. **Toda autorización conversacional (voz o texto) exige la clave.** Antes
   bastaba decir "autoriza run_command" (solo requería principal raíz), así
   que cualquier persona frente al micrófono/teclado podía auto-concederse
   permisos. Ahora la concesión por lenguaje natural pide la clave del owner:

   - `security/owner_secret.py` → clase `OwnerSecret`. Lee la clave de la
     variable de entorno `ILU_OWNER_SECRET` (el valor ES la clave, de mayor
     precedencia) o del archivo local `security/owner.pin` (gitignored,
     mismo nivel que `security/device.key`). La lectura es perezosa; la
     comparación usa `secrets.compare_digest`.
   - `config/settings.py` → `owner_secret_path` (env `ILU_OWNER_SECRET_PATH`,
     default `security/owner.pin`).
   - `app/core.py` → `_authority_command` (solo el flujo `grant_prefixes`):
     extrae el PIN con `re.findall(r"\b\d{6}\b", message)`, lo REMUEVE del
     `target` antes de parsear la capacidad (el PIN puede ir al final o
     antepuesto por "autoriza con clave", v. ej. en los tests, donde el
     valor se inyecta desde `ILU_OWNER_SECRET`) y aplica el gate:

     1. `capability_prohibited` PRIMERO y sin clave (a quien intenta
        "autoriza shell" se le rechaza idéntico a antes, sin pedir nada).
     2. Clave no configurada → bloqueado (`owner_pin_unconfigured`).
     3. Clave ausente en el mensaje → pide la clave (`owner_pin_required`).
     4. Clave incorrecta → deniega (`owner_pin_denied`) y audita
        `owner_secret_failed` con `reason=wrong_pin`, `decision=deny`.
     5. Clave válida → concede exactamente como antes.

   **El PIN jamás va en el system prompt ni se le muestra al modelo**: se
   valida solo en código determinista, fail-closed. "Sin clave" nunca
   significa "todo permitido".

### Alcance (qué NO cambió)

- La jerarquía de seguridad queda intacta: conceder, revocar, cambiar
  autonomía y resolver solicitudes siguen exigiendo que el ACTOR sea la
  raíz (`owner`). El PIN prueba identidad de la persona; no confiere root
  a nadie que no lo sea.
- El token de dispositivo (`security/device.key`) sigue siendo una
  credencial válida de la máquina para la interfaz web (defensa en
  profundidad; no se eliminó ningún control).
- Cambiar autonomía y revocar permisos por NL siguen exigiendo principal
  raíz SIN PIN (se puede ampliar a pedido; queda documentado como límite).
- El modelo no conoce la clave y no la puede revelar ni confirmar.

### Mismo secreto del owner en la web (unificación)

Desde el arranque de la interfaz HTTP, las rutas de autoridad (conceder
permisos, resolver solicitudes, cambiar autonomía, borrar conversaciones)
aceptan **el mismo secreto del owner** que usa la concesión por
voz/texto, además del token de dispositivo:

- Web (`app/web/js/ui.js` + `api.js`): las acciones admin piden la
  identidad (actor, `owner`) Y el PIN; el PIN se guarda en `sessionStorage`
  (NO persiste entre sesiones del navegador) y viaja en la cabecera
  `X-ILU-Pin`. `ILUApi.setPin(...)` lo configura desde la consola.
- Server (`app/__main__.py::_authorized`): devuelve verdadero si el
  request demuestra el token de dispositivo **o** el PIN del owner. El PIN
  se valida con `OwnerSecret.matches` (tiempo constante); un PIN
  incorrecto deja `owner_secret_failed` con `reason=wrong_pin`,
  `method=http_x_ilu_pin`.
- **Fail-closed**: si el PIN no está configurado, el camino del PIN no
  autoriza; queda solo el token de dispositivo. El valor del secreto jamás
  aparece en logs, auditorías, errores ni respuestas HTTP.

Causa del "unauthorized" histórico: el gate web exigía el token de
dispositivo en el navegador ANTES de evaluar al actor, así que "owner" no
podía pasar aunque fuera la identidad correcta. Al habilitar el PIN como
credencial de persona, la interfaz acepta `owner` + clave.

### Verificación

- `py_compile` de los módulos tocados · `git diff --check`.
- `pytest` selectivo: `test_owner_secret`, `test_creator_identity`,
  `test_core_authorization_nl`, `test_identity`, `test_principal` + regresiva
  de seguridad (`test_security_gate_grants`, `test_authority`).
- Smoke HTTP (stores en tmp, clave por entorno `ILU_OWNER_SECRET`): el
  mensaje de concesión sin clave pide la clave y NO concede; con la clave
  correcta concede; con una clave incorrecta deniega y deja
  `owner_secret_failed` en el audit. (La clave se carga del mecanismo
  seguro; su valor no se documenta.)
- Smoke web: `POST /grants` con `X-ILU-Pin` (la clave del owner cargada
  del mecanismo seguro) y `actor=owner` concede; con una clave inválida
  devuelve `unauthorized` y queda `owner_secret_failed` (method
  `http_x_ilu_pin`) en el audit; con la clave válida pero actor no-root
  devuelve `403 no_autoridad_raiz`.
- **Contrato de awareness en respuestas de herramientas.** En una suite
  E2E con el proveedor real apareció una falla intermitente:
  `test_awareness_injected_into_response` a veces fallaba porque el modelo
  decidía proponer una herramienta ante un mensaje casual ("me gusta el
  café"). El camino de herramientas salía temprano en `process()` sin
  adjuntar `awareness` (defecto preexistente, ajeno a este bloque). Se
  corrigió: `_build_tool_response` ahora incluye `awareness` y
  `awareness_context` tanto en el dict de éxito como en el de error, así
  el contrato "la conciencia unificada viaja con la respuesta" se cumple
  sin importar lo que el proveedor decida. Queda cubierto por tests
  deterministas (`test_tool_error_transporta_awareness`,
  `test_tool_ok_transporta_awareness`) además del E2E.

### Limitaciones honestas

- `security/owner.pin` se guarda EN CLARO local (gitignored), mismo nivel que
  `device.key`: si la máquina se compromete, conviene rotar la clave.
- El PIN es de 6 dígitos y cubre solo la concesión por voz/texto; no
  protege la UI (que usa el device token).
