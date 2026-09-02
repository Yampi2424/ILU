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

Bloque 8 — única puerta de ejecución. SecurityGate sigue siendo el ÚNICO
punto donde se decide. Cuando se le inyectan grant_store / policy /
emergency (todos opcionales), consulta:
    - acciones PROHIBIDAS por policy  -> denegadas siempre,
    - protocolos de emergencia activos -> autorizan la capacidad,
    - grants activos del actor         -> auto-aprueban una tool "ask"
                                          en autonomía asistida/autónoma.
Sin estos componentes inyectados, el comportamiento es idéntico al
anterior (retrocompatible).

La compuerta JAMÁS se auto-concede permisos: solo decide sobre
autorizaciones emitidas por Authority (que a su vez solo responde a un
principal humano raíz).

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

    def decide(
        self,
        tool_name,
        permission,
        mode="direct",
        capability=None,
        actor=None,
        context=None,
        grant_store=None,
        policy=None,
        emergency=None,
        spoofing=None,
        verification_ok=True
    ):
        """
        Decide si una herramienta puede ejecutarse.

        mode:
            "direct" — herramienta determinista identificada por I.L.U.
            "model"  — herramienta propuesta por el modelo.

        Opcionales (Bloque 8; si no se pasan, comportamiento previo):
            capability       — nombre de capacidad (p. ej. "write_file")
            actor            — identidad del principal que solicita
            context          — dict con task_id/project/device context
            grant_store      — GrantStore para consultar grants activos
            policy           — Policy para acciones prohibidas
            emergency        — EmergencyRegistry para protocolos activos
            spoofing         — SpoofingGuard para verificación de identidad
            verification_ok  — ¿pasó la verificación de identidad?

        Devuelve:
            {"decision": "allow"|"ask"|"deny", "reason": str, "tool": ...}
        """

        if permission == "blocked":
            return {
                "decision": "deny",
                "reason": "tool_blocked",
                "tool": tool_name
            }

        # --- Bloque 8: política (acciones prohibidas nunca se ejecutan) ---
        if (
            policy is not None
            and capability
            and policy.is_prohibited(capability)
        ):
            return {
                "decision": "deny",
                "reason": "prohibited_action",
                "tool": tool_name
            }

        # --- Bloque 8: verificación de identidad (detección de spoofing) ---
        if (
            spoofing is not None
            and capability
            and not verification_ok
            and policy is not None
            and policy.sensitivity(capability) == "high"
        ):
            suspected = spoofing.record_failure(
                actor or "unknown",
                capability,
                context=context or {},
            )

            if suspected:
                return {
                    "decision": "deny",
                    "reason": "identity_suspected",
                    "tool": tool_name
                }

        # --- Bloque 8: protocolo de emergencia activo autoriza ---
        if (
            emergency is not None
            and capability
            and emergency.covers(capability) is not None
        ):
            return {
                "decision": "allow",
                "reason": "emergency_protocol",
                "tool": tool_name
            }

        if permission == "ask":
            # Un grant ACTIVO emitido por Authority puede auto-aprobar la
            # ejecución en autonomía asistida o autónoma (ya hubo una
            # autorización humana explícita previa). En manual siempre se
            # pregunta, incluso con grant: la autonomía manual no delega
            # la decisión en grants.
            if (
                grant_store is not None
                and capability is not None
                and self.autonomy_level in ("assisted", "autonomous")
            ):
                grant = grant_store.find_active(capability, actor, context)

                if grant is not None:
                    return {
                        "decision": "allow",
                        "reason": "granted",
                        "tool": tool_name,
                        "grant_id": grant.key,
                    }

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