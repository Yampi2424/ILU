"""
I.L.U. — Integración con Dispositivos y Computador (Bloque F: JARVIS).

I.L.U. debe integrarse progresivamente con el computador, dispositivos y
servicios AUTORIZADOS. Este módulo define un catálogo de acciones sobre el
mundo, cada una con su CAPACIDAD (para el SecurityGate).

Regla de seguridad inviolable:
  - Una integración NUNCA se ejecuta directa ni por capricho: pasa por
    `execute(capability, ...)`, que exige un grant activo para esa
    capacidad y registra todo en el audit log.
  - En modo manual, cualquier integración devuelve `authorization=ask`
    y abre la solicitud, salvo que ya exista un grant.

Qué hay REAL hoy:
  - workspace_write: escribir un archivo DENTRO del workspace.
  - workspace_list:  listar el workspace (solo lectura).
  - run_command:     ejecutar un comando de la LISTA BLANCA (Bloque 13):
                     shell=False, timeout y salida acotada.
  - open_app:        abrir una aplicación de la lista blanca de apps.
  - media_control:   controlar multimedia vía un backend permitido
                     (playerctl), con acciones acotadas.

Qué queda PLANIFICADO (arquitectura lista, requeriría permisos OS/red):
  - device_control: el catálogo declara la capacidad, pero `_run`
    devuelve "not_implemented" con un motivo claro.

Seguridad (Bloque 13): las integraciones del mundo pasan SIEMPRE por
`execute()`, que exige un grant activo para la capacidad (salvo que la
compuerta ya haya decidido, vía pre_authorized=True). QUÉ se puede ejecutar
/ abrir / controlar vive en `security/run_commands.json` (CommandPolicy):
el grant autoriza la CAPACIDAD, la política decide la LISTA BLANCA. `shell`
crudo sigue prohibido en policy.json.
"""

import os
import shutil
import subprocess

from security.command_policy import CommandPolicy


class IntegrationManager:
    """
    Catálogo de acciones sobre el mundo, gateadas por capacidad.

    Cada integración declara:
      - capability:  nombre que el SecurityGate usa para el grant.
      - description: qué hace (para el modelo y el owner).
      - read_only:   True si no modifica nada (percepción/lectura).
    """

    # capability -> dict(description, read_only)
    CATALOG = {
        "workspace_list": {
            "description": "Listar archivos del workspace (solo lectura)",
            "read_only": True,
        },
        "workspace_write": {
            "description": "Escribir un archivo dentro del workspace",
            "read_only": False,
        },
        "media_control": {
            "description": (
                "Controlar reproducción multimedia vía el backend permitido "
                "(playerctl), con acciones acotadas."
            ),
            "read_only": False,
        },
        "open_app": {
            "description": (
                "Abrir una aplicación de la lista blanca del sistema."
            ),
            "read_only": False,
        },
        "run_command": {
            "description": (
                "Ejecutar un comando de la lista blanca (shell=False, "
                "timeout y salida acotada)."
            ),
            "read_only": False,
        },
        "device_control": {
            "description": "Controlar un dispositivo autorizado (PLANIFICADO)",
            "read_only": False,
        },
    }

    def __init__(self, workspace=None, security=None, audit=None,
                 grant_store=None):
        self.workspace = os.path.abspath(
            workspace or os.environ.get(
                "ILU_WORKSPACE",
                os.getcwd(),
            )
        )
        self.security = security       # SecurityGate (decide)
        self.audit = audit             # AuditLog
        self.grant_store = grant_store # GrantStore (grants activos)

        # Lista blanca del mundo (Bloque 13): qué comandos/apps/acciones de
        # media están permitidos y los confinamientos (timeout, max output).
        self.command_policy = CommandPolicy()

    # ------------------------------------------------------------------
    # Catálogo
    # ------------------------------------------------------------------

    def list_capabilities(self):
        return [
            {
                "capability": capability,
                "description": meta["description"],
                "read_only": meta["read_only"],
                "implemented": self._implemented(capability),
            }
            for capability, meta in self.CATALOG.items()
        ]

    def has_capability(self, capability):
        return capability in self.CATALOG

    @staticmethod
    def _implemented(capability):
        # Integraciones reales: workspace + ejecución gateada (Bloque 13).
        # device_control sigue PLANIFICADO (requiere permisos OS/red).
        return capability in (
            "workspace_list",
            "workspace_write",
            "run_command",
            "open_app",
            "media_control",
        )

    # ------------------------------------------------------------------
    # Ejecución gateada
    # ------------------------------------------------------------------

    def execute(self, capability, actor="ilu", pre_authorized=False, **kwargs):
        """
        Ejecuta una integración SOLO con un grant activo.

        Devuelve un dict con `success` o con `authorization=ask` si falta
        el permiso. Nunca ejecuta sin autorización.

        pre_authorized=True marca que la compuerta (SecurityGate) YA decidió
        allow para ESTA llamada (camino del core: las tools run_command /
        open_app / media_control pasan primero por ella). Así un grant de uso
        único no se consume DOS veces (una en la compuerta y otra aquí).
        Los caminos que NO pasan por la compuerta (p. ej. la proactividad)
        llaman sin pre_authorized y la integración hace su propio check.
        """
        if not self.has_capability(capability):
            return {
                "success": False,
                "error": "capability_not_in_catalog",
                "capability": capability,
            }

        if not self._implemented(capability):
            return {
                "success": False,
                "error": "not_implemented",
                "capability": capability,
                "reason": (
                    "Integración planificada: requiere permisos del "
                    "sistema operativo o de red aún no habilitados."
                ),
            }

        if not pre_authorized and not self._authorized(capability, actor):
            return {
                "success": False,
                "error": "authorization_required",
                "capability": capability,
                "authorization": "ask",
            }

        try:
            result = self._run(capability, **kwargs)
        except Exception as error:
            if self.audit:
                self.audit.record(
                    actor=actor,
                    action="integration",
                    capability=capability,
                    success=False,
                    error="integration_failed",
                )
            return {
                "success": False,
                "error": "integration_failed",
                "detail": str(error),
                "capability": capability,
            }

        if self.audit:
            self.audit.record(
                actor=actor,
                action="integration",
                capability=capability,
                success=result.get("success", False),
            )

        return result

    def _authorized(self, capability, actor):
        """¿Hay un grant activo para esta capacidad?"""
        if self.grant_store is None:
            return False

        grants = self.grant_store.list(
            capability=capability,
            status="active",
        )

        return bool(grants)

    # ------------------------------------------------------------------
    # Implementaciones reales (workspace)
    # ------------------------------------------------------------------

    def _run(self, capability, **kwargs):
        if capability == "workspace_list":
            return self._workspace_list()
        if capability == "workspace_write":
            return self._workspace_write(kwargs)
        if capability == "run_command":
            return self._run_command(kwargs)
        if capability == "open_app":
            return self._open_app(kwargs)
        if capability == "media_control":
            return self._media_control(kwargs)
        return {
            "success": False,
            "error": "not_implemented",
        }

    # ------------------------------------------------------------------
    # Ejecución real gateada (Bloque 13): run_command / open_app / media
    # ------------------------------------------------------------------

    def _run_command(self, kwargs):
        command = kwargs.get("command")
        timeout = kwargs.get("timeout") or self.command_policy.default_timeout()

        ok, value = self.command_policy.validate_command(command)
        if not ok:
            return {
                "success": False,
                "error": value,
                "command": command if isinstance(command, str) else None,
            }

        try:
            timeout = max(1, int(timeout))
        except (TypeError, ValueError):
            timeout = self.command_policy.default_timeout()

        try:
            completed = subprocess.run(
                value,
                shell=False,
                capture_output=True,
                timeout=timeout,
                text=True,
            )
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": "command_timeout",
                "command": value[0],
            }
        except (OSError, ValueError) as error:
            return {
                "success": False,
                "error": "command_execution_failed",
                "detail": str(error),
                "command": value[0],
            }

        limit = self.command_policy.max_output_bytes()
        stdout = completed.stdout[-limit:] if completed.stdout else ""
        stderr = completed.stderr[-limit:] if completed.stderr else ""

        truncated = (
            bool(stdout) and len(completed.stdout) > limit
        ) or (
            bool(stderr) and len(completed.stderr) > limit
        )

        return {
            "success": True,
            "command": value[0],
            "exit_code": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": truncated,
        }

    def _open_app(self, kwargs):
        app = (kwargs.get("app") or "").strip()
        if not app:
            return {
                "success": False,
                "error": "app_required",
            }
        if not self.command_policy.app_allowed(app):
            return {
                "success": False,
                "error": "app_not_allowed",
                "app": app,
            }
        if shutil.which(app) is None:
            return {
                "success": False,
                "error": "app_not_found",
                "app": app,
            }

        try:
            process = subprocess.Popen(
                [app],
                shell=False,
                start_new_session=True,
            )
        except (OSError, ValueError) as error:
            return {
                "success": False,
                "error": "app_launch_failed",
                "detail": str(error),
                "app": app,
            }

        pid = getattr(process, "pid", None)
        return {
            "success": True,
            "app": app,
            "pid": pid,
        }

    def _media_control(self, kwargs):
        action = (kwargs.get("action") or "").strip()

        args, backend = self.command_policy.media_args(action)
        if args is None or backend is None:
            return {
                "success": False,
                "error": "media_action_invalid",
                "action": action,
            }
        if shutil.which(backend) is None:
            return {
                "success": False,
                "error": "media_backend_unavailable",
                "backend": backend,
            }

        try:
            completed = subprocess.run(
                [backend] + args,
                shell=False,
                capture_output=True,
                timeout=self.command_policy.default_timeout(),
                text=True,
            )
        except (OSError, ValueError) as error:
            return {
                "success": False,
                "error": "media_control_failed",
                "detail": str(error),
                "action": action,
            }

        return {
            "success": completed.returncode == 0,
            "action": action,
            "backend": backend,
            "exit_code": completed.returncode,
            "detail": (completed.stderr or "").strip()[:500],
        }

    def _workspace_list(self):
        try:
            entries = sorted(os.listdir(self.workspace))[:100]
        except OSError as error:
            return {
                "success": False,
                "error": str(error),
            }

        return {
            "success": True,
            "workspace": self.workspace,
            "entries": entries,
            "count": len(entries),
        }

    def _workspace_write(self, kwargs):
        filename = (kwargs.get("filename") or "").strip()
        content = kwargs.get("content") or ""

        if not filename:
            return {
                "success": False,
                "error": "filename_required",
            }

        # Seguridad de ruta: solo dentro del workspace.
        target = os.path.abspath(
            os.path.join(self.workspace, filename)
        )

        if not target.startswith(self.workspace + os.sep):
            return {
                "success": False,
                "error": "path_outside_workspace",
            }

        try:
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(content)
        except OSError as error:
            return {
                "success": False,
                "error": str(error),
            }

        return {
            "success": True,
            "written": filename,
        }
