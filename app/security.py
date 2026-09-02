"""
Compuerta de autorización de I.L.U.

Una respuesta del modelo NUNCA equivale a permiso de ejecución.
Toda herramienta debe pasar por esta compuerta antes de ejecutarse.

Niveles de autonomía (variable ILU_AUTONOMY):
    manual     — I.L.U. no ejecuta por su cuenta las herramientas
                 que propone el modelo; las presenta para
                 autorización.
    assisted   — I.L.U. ejecuta herramientas seguras; las que
                 requieren autorización se detienen en la compuerta.
    autonomous — por ahora se comporta igual que assisted: sin un
                 registro de autorizaciones previas del usuario,
                 las herramientas tipo "ask" siguen exigiendo autorización
                 humana. (El auto-aprobado es PLANIFICADO y requiere
                 reglas explícitas de autorización.)

Permisos de herramienta (registro ToolManager):
    safe     — solo lectura / inocua
    ask      — requiere autorización humana explícita
    blocked  — prohibida

Cierre a prueba de fallos: permiso desconocido se trata como denegado.
"""

import os


class SecurityGate:
    AUTONOMY_LEVELS = ("manual", "assisted", "autonomous")

    def __init__(self, autonomy_level=None):
        raw = (
            autonomy_level
            or os.environ.get("ILU_AUTONOMY", "assisted")
        )

        if (
            isinstance(raw, str)
            and raw.lower() in self.AUTONOMY_LEVELS
        ):
            self.autonomy_level = raw.lower()
        else:
            self.autonomy_level = "assisted"

    def decide(self, tool_name, permission, mode="direct"):
        """
        Decide si una herramienta puede ejecutarse.

        mode:
            "direct" — herramienta determinista identificada por I.L.U.
            "model"  — herramienta propuesta por el modelo.

        Devuelve:
            {"decision": "allow"|"ask"|"deny", "reason": str, "tool": ...}
        """

        if permission == "blocked":
            return {
                "decision": "deny",
                "reason": "tool_blocked",
                "tool": tool_name
            }

        if permission == "ask":
            # La autorización humana es obligatoria en todo nivel
            # de autonomía mientras no exista un registro de
            # autorizaciones previas (PLANIFICADO).
            return {
                "decision": "ask",
                "reason": "authorization_required",
                "tool": tool_name
            }

        if permission != "safe":
            return {
                "decision": "deny",
                "reason": "unknown_permission",
                "tool": tool_name
            }

        if mode == "model" and self.autonomy_level == "manual":
            return {
                "decision": "ask",
                "reason": "manual_mode_proposal",
                "tool": tool_name
            }

        return {
            "decision": "allow",
            "reason": "allowed_safe_tool",
            "tool": tool_name
        }