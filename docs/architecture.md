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
