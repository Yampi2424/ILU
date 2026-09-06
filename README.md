# I.L.U. — Intelligent Local/Cloud Universal Assistant

Asistente inteligente con **identidad femenina propia** (presencia etérea de plasma, voz femenina, animaciones orgánicas) que alcanza las **capacidades de OpenJarvis** (auditado, no clonado) con **experiencia de primer nivel**.

> **Fórmula:** `OPENJARVIS CAPABILITIES + FIRST-CLASS EXPERIENCE + FEMININE IDENTITY = I.L.U.`

---

## Estado actual (2026-09-06)

| Bloque | Capacidad | Estado |
|--------|-----------|--------|
| **1–2** | Núcleo, percepción, planificación, aprendizaje | ✅ |
| **3** | Autorización + Auditoría (SecurityGate, Authority, grants durables, PIN owner) | ✅ |
| **4** | Tareas en segundo plano + concurrencia | ✅ |
| **5** | Memoria versionada, multi-backend, router único | ✅ |
| **6** | Panel de 5 herramientas gateadas (`system_time`, `web_search`, `read_file`, `notify`, `write_file`) | ✅ |
| **7** | Sub-agentes secuenciales (misma compuerta, misma autoridad) | ✅ |
| **8** | Autoridad gobernada (PrincipalRegistry, Policy, GrantStore, DeviceRegistry, Emergency, SpoofingGuard, AuthRequestStore) | ✅ |
| **9** | Tool-calling nativo + Fallback cloud→local (OmniRoute→Ollama) | ✅ |
| **10** | Historial multi-turn (ConversationStore, sesión, inyección contexto) | ✅ |
| **11** | JSON-schema por herramienta + validación fail-closed | ✅ |
| **12** | Búsqueda semántica de memoria (recall por significado) | ✅ |
| **13** | Ejecución real gateada (`run_command`, `open_app`, `media_control` con whitelist) | ✅ |
| **14** | Identidad del creador + PIN de autorización (voz/texto + web unificado) | ✅ |
| **15** | **Streaming SSE** — Proveedores stream + `process_stream` + endpoint `/ask/stream` | ✅ |
| **16** | **Skills** — Catálogo `SKILL.md`, ejecución gateada (`mode="skill"`), 6 skills, `web_fetch` SSRF | ✅ |
| **17** | **Scheduler + Agentes** — Jobs persistentes (interval/cron), daemon tick, proactividad centralizada | ✅ |
| **18** | **Deep Research** — Descomponer → buscar/fetch → sintetizar con fuentes, background | ✅ |
| **19** | **Memoria ingest + consolidación** — Local workspace, chunks, no destructiva, browser endpoints | ✅ |
| **20** | **Diagnostics + Benchmark** — Health checks + benchmark offline/live JSONL | ✅ |
| **21** | **Frontend femenino-plasma** — Inmersivo, streaming, dock/paneles glass, PIN modal elegante, voz segmentada | ✅ |
| **22** | **Seguridad transversal + cierre** — Verificación cross-cutting, 753 tests, smoke real, docs, commit | ✅ |

**Tests:** 753 passed (suite completa sin hang)
**Lint:** `py_compile` OK · `git diff --check` OK
**Secrets:** `security/owner.pin` y `security/omniroute.key` en `.gitignore` — **nunca en repo**

---

## Arquitectura de seguridad (no negociable)

- **OWNER = autoridad máxima**. Ningún agente, skill, tool o subagente se concede permisos a sí mismo.
- **Toda ejecución** atraviesa `core._execute_tool_call()` → `SecurityGate.decide` + `AuditLog`.
- **Grants durables** del owner funcionan sin re-pedir; acciones nuevas piden → `AuthorizationRequestStore`.
- **Secreto owner**: jamás hardcodeado ni expuesto. Entorno `ILU_OWNER_SECRET` / archivo `security/owner.pin` (gitignored). Comparación `secrets.compare_digest`.
- **UI**: token dispositivo (`localStorage` `ilu_device_token`) + `X-ILU-Pin` (`sessionStorage` `ilu_owner_pin`). PIN **nunca** en logs/audit/HTTP/commits/respuestas.
- **Sin auto-grant** en: SkillManager, AgentManager, Scheduler, DeepResearch, Ingest, Consolidation.

---

## Inicio rápido

```bash
# 1. Clonar y entrar
git clone <repo> && cd ILU

# 2. Configurar secreto del owner (6 dígitos)
echo "123456" > security/owner.pin
# o export ILU_OWNER_SECRET=123456

# 3. (Opcional) OmniRoute cloud
export ILU_OMNIROUTE_API_KEY=...
export ILU_OMNIROUTE_URL=https://api.omniroute.xyz/v1
export ILU_OMNIROUTE_MODEL=gpt-4o-mini

# 4. (Opcional) Ollama local para fallback
export ILU_LOCAL_MODEL=llama3.1
export ILU_OLLAMA_URL=http://localhost:11434

# 5. Arrancar
python -m app

# 6. Abrir http://localhost:8000
# - Conceder permisos en panel Permisos (requiere PIN)
# - Probar: "¿qué hora es?", "busca en internet IA", "ejecutá la skill investigacion con tema=clima"
```

---

## Endpoints clave

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/healthz` | Salud + versión |
| GET | `/about` | Info completa (memoria, tasks, provider, autonomía) |
| POST | `/ask` | Chat síncrono (JSON) |
| POST | `/ask/stream` | **Chat streaming SSE** (`?stream=1` o `Accept: text/event-stream`) |
| GET | `/skills` | Catálogo de skills |
| POST | `/skills/<name>/run` | Ejecutar skill (variables + PIN admin) |
| GET/POST | `/agents` | CRUD agentes programados |
| POST | `/agents/<id>/run` | Correr agente ya |
| GET/POST | `/scheduler` | CRUD jobs (interval/cron, kinds: agent/reminder/monitor/digest/consolidation) |
| POST | `/research/run` | Deep research (background) |
| POST | `/memory/ingest` | Ingestar archivo/carpeta del workspace |
| GET | `/memory/search?q=` | Buscar memoria |
| GET | `/diagnostics` | Health checks |
| POST | `/diagnostics/check` | Ejecutar checks reales |
| GET | `/benchmark` | Historial benchmarks |
| POST | `/benchmark/run` | Correr benchmark |
| GET | `/conversations/<id>` | Historial de sesión |
| GET | `/security` | Estado seguridad (grants, policy, autonomía, device, owner) |

> **Mutaciones requieren admin auth**: token dispositivo **o** `X-ILU-Pin` (PIN owner).

---

## Frontend — Experiencia inmersiva

Al abrir `http://localhost:8000`:

1. **Presencia central** — Canvas plasma femenino (`ilu-plasma.js`) con aura reactiva al estado
2. **Órbita de estado** — Etiqueta natural: "Te escucho", "Pensando…", "Trabajando…", "Espero tu autorización…", "Aprendí algo nuevo"
3. **Chat-shell** — Vidrio flotante con **streaming token a token**, action-cards colapsables en lenguaje natural
4. **Dock minimal** — 8 iconos (Conversar, Tareas, Agentes, Skills, Memoria, Actividad, Permisos, Sistema) → drawers glass laterales
5. **PIN modal elegante** — Overlay glow, **nunca `prompt()`**, guarda en `sessionStorage`, envía `X-ILU-Pin`
6. **Voz femenina segmentada** — TTS por frases (empieza a hablar antes de terminar), visualizador → plasma, barge-in
7. **Responsive** — Desktop lado a lado, <900px apilado, <600px dock bottom-sheet

---

## Variables de entorno principales

| Variable | Default | Descripción |
|----------|---------|-------------|
| `ILU_OWNER_SECRET` | — | PIN 6 dígitos (precedencia sobre archivo) |
| `ILU_OWNER_SECRET_PATH` | `security/owner.pin` | Ruta archivo PIN |
| `ILU_AI_PROVIDER` | `local` | `local` \| `omniroute` |
| `ILU_LOCAL_MODEL` | `llama3.1` | Modelo Ollama |
| `ILU_OLLAMA_URL` | `http://localhost:11434` | URL Ollama |
| `ILU_OLLAMA_TIMEOUT` | `600` | Timeout local (s) |
| `ILU_OMNIROUTE_API_KEY` | — | Clave OmniRoute |
| `ILU_OMNIROUTE_URL` | `https://api.omniroute.xyz/v1` | Base URL OmniRoute |
| `ILU_OMNIROUTE_MODEL` | `gpt-4o-mini` | Modelo OmniRoute |
| `ILU_FALLBACK_TIMEOUT_CAP` | `120` | Tope timeout fallback (s) |
| `ILU_AUTONOMY` | `assisted` | `manual` \| `assisted` \| `autonomous` |
| `ILU_MEMORY_BACKEND` | `json` | `json` \| `postgres` |
| `DATABASE_URL` | — | Postgres (Neon u otro) |
| `ILU_HISTORY_TURNS` | `6` | Turnos de historial inyectados |
| `ILU_SCHEDULER_TICK` | `15` | Intervalo tick scheduler (s) |
| `ILU_WORKSPACE` | `cwd` | Raíz workspace para tools de archivo |
| `ILU_TTS_VOICE` | `es-AR-ElenaNeural` | Voz Edge TTS |
| `ILU_TTS_RATE` | `+0%` | Velocidad TTS |

---

## Desarrollo

```bash
# Tests
pytest tests/ -x -q              # Suite completa (753 tests)
pytest tests/test_skills.py -v   # Tests específicos

# Lint
python -m py_compile app/**/*.py
git diff --check

# Smoke server temporal (puerto 8001, stores tmp)
ILU_OWNER_SECRET=123456 python -m app &
# curl :8001/healthz, :8001/diagnostics, :8001/ask/stream...
```

---

## Documentación

- `docs/architecture.md` — Arquitectura completa (Bloques 1–22 con decisiones, verificaciones, limitaciones honestas)
- `app/skills_catalog/README.md` — Formato SKILL.md y skills incluidas
- `config/settings.py` — Configuración central tipada

---

## Créditos

Desarrollado con **Claude Code** (Anthropic).
Identidad visual: plasma femenino, presencia etérea, voz femenina.
Inspirado en capacidades de **OpenJarvis** (auditado, no clonado).

---

**I.L.U. no es un chatbot con memoria agregada. Es una arquitectura de inteligencia con memoria, autoridad, proactividad, percepción y presencia propia.**