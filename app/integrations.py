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

Qué hay REAL hoy (inocuo y acotado):
  - workspace_write: escribir un archivo DENTRO del workspace.
  - workspace_list:  listar el workspace (solo lectura).

Qué queda PLANIFICADO (arquitectura lista, requeriría permisos OS/red):
  - media_control, open_app, run_command, device_control: el catálogo
    declara su capacidad, pero `_run` devuelve "not_implemented" con un
    motivo claro. Cuando se autorice, solo hay que implementar `_run`.
"""

import os


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
            "description": "Controlar reproducción multimedia (PLANIFICADO)",
            "read_only": False,
        },
        "open_app": {
            "description": "Abrir una aplicación del sistema (PLANIFICADO)",
            "read_only": False,
        },
        "run_command": {
            "description": "Ejecutar un comando autorizado (PLANIFICADO)",
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
        # Solo las integraciones de workspace están implementadas hoy.
        return capability in ("workspace_list", "workspace_write")

    # ------------------------------------------------------------------
    # Ejecución gateada
    # ------------------------------------------------------------------

    def execute(self, capability, actor="ilu", **kwargs):
        """
        Ejecuta una integración SOLO con un grant activo.

        Devuelve un dict con `success` o con `authorization=ask` si falta
        el permiso. Nunca ejecuta sin autorización.
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

        if not self._authorized(capability, actor):
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
        return {
            "success": False,
            "error": "not_implemented",
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
