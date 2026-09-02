"""
Sub-agentes y sub-assistant anidado (Bloque 7).

I.L.U. puede delegar una sub-tarea a un contexto de conversación fresco y
enfocado que comparte el MISMO proveedor, toolset y compuerta de seguridad
que el flujo principal.

Reglas de seguridad:
- El sub-agente hereda SecurityGate y AuditLog del padre (mismas instancias).
- NUNCA eleva permisos: una herramienta que requiere autorización se
  detiene en la compuerta exactamente igual que en el flujo principal.
- La ejecución de herramientas del sub-agente se audita (action
  "tool_attempt"/"tool_result") y el propio sub-agente se audita como
  action "subagent".
- Las tools propuestas por el sub-agente respetan el nivel de autonomía del
  padre: en modo "manual" una tool "safe" propuesta por el (sub)modelo se
  detiene en "ask", igual que en el flujo principal.

Alcance (Bloque 7):
- Sub-assistant SECUENCIAL con tope de rondas (max_rounds).
- Contexto acotado y memoria separada (no escribe en el hilo principal).
- Devuelve un resultado acotado al padre (nunca acceso directo al toolset
  del padre).
- NO: paralelismo real, aprendizaje, modificación del propio código ni
  autonomía adicional (todo eso es PLANIFICADO).
"""

from tools.call import ToolCall


class SubAgent:
    """
    Sub-assistant secuencial que reutiliza el proveedor, el toolset, la
    compuerta de seguridad y el auditor del contexto padre.
    """

    def __init__(
        self,
        provider,
        tools,
        security,
        audit,
        memory=None,
        max_rounds=3,
        grant_store=None,
        policy=None,
        emergency=None
    ):
        self.provider = provider
        self.tools = tools
        self.security = security
        self.audit = audit
        self.memory = memory
        self.max_rounds = max_rounds
        # Bloque 8: el sub-agente CONSULTA los grants emitidos por la
        # Authority del padre (hereda la autorización del contexto padre),
        # pero NUNCA posee ni referencia Authority: no puede autoconcederse
        # ni elevar nivel alguno.
        self.grant_store = grant_store
        self.policy = policy
        self.emergency = emergency

    # ------------------------------------------------------------------
    # Contexto acotado (memoria separada)
    # ------------------------------------------------------------------

    def _recall_sub_context(self, prompt):
        """
        Contexto de trabajo del sub-agente: recuerdos tipo working/personal
        vía MemoryRouter, si está disponible. Nunca escribe memoria.
        """
        memory = self.memory

        if memory is None:
            return []

        recall = getattr(memory, "recall_context", None)

        if not callable(recall):
            return []

        try:
            items = recall(prompt, top_k=3) or []
        except Exception:
            return []

        return self._format_items(items)

    @staticmethod
    def _format_items(items):
        if not items:
            return []

        values = []

        for item in items:
            content = (
                item.get("content")
                if isinstance(item, dict)
                else None
            )

            if content:
                values.append(str(content))

        return values

    # ------------------------------------------------------------------
    # Ejecución de herramientas (vía la compuerta del padre)
    # ------------------------------------------------------------------

    def _execute_tool_call(self, tool_call, mode="subagent"):
        """
        Ejecuta una ToolCall del sub-agente pasando por la MISMA compuerta
        (SecurityGate) y el MISMO auditor del padre. El sub-agente nunca
        eleva permisos: una tool "ask" se detiene aquí.
        """
        if tool_call is None:
            return None

        if not self.tools.has_tool(tool_call.tool):
            self.audit.record(
                actor="ilu",
                action="tool_attempt",
                tool=tool_call.tool,
                decision="deny",
                mode=mode,
                reason="tool_not_available"
            )

            return {
                "success": False,
                "error": "tool_not_available",
                "tool": tool_call.tool
            }

        permission = self.tools.get_permission(
            tool_call.tool
        )

        # mode="model" para que la regla de autonomía manual del padre se
        # aplique igual que a las tools propuestas por el modelo principal
        # (en manual, una tool "safe" propuesta por el modelo → "ask").
        #
        # Bloque 8: el sub-agente consulta los grants del padre (los
        # mismos que aprobaría I.L.U. en modo autónomo); no tiene acceso
        # a Authority, así que jamás puede concederse permisos nuevos.
        decision = self.security.decide(
            tool_call.tool,
            permission,
            mode="model",
            capability=tool_call.tool,
            actor="ilu",
            context={},
            grant_store=self.grant_store,
            policy=self.policy,
            emergency=self.emergency,
        )

        self.audit.record(
            actor="ilu",
            action="tool_attempt",
            tool=tool_call.tool,
            permission=permission,
            decision=decision["decision"],
            mode=mode,
            reason=decision["reason"]
        )

        if decision["decision"] != "allow":
            return {
                "success": False,
                "error": decision["reason"],
                "tool": tool_call.tool,
                "authorization": decision["decision"]
            }

        try:
            result = self.tools.execute(
                tool_call.tool,
                **tool_call.arguments
            )

        except Exception as error:
            self.audit.record(
                actor="ilu",
                action="tool_result",
                tool=tool_call.tool,
                success=False,
                error="tool_execution_failed"
            )

            return {
                "success": False,
                "error": str(error),
                "tool": tool_call.tool
            }

        self.audit.record(
            actor="ilu",
            action="tool_result",
            tool=tool_call.tool,
            success=result.get("success", False)
        )

        return result

    # ------------------------------------------------------------------
    # Bucle secuencial acotado
    # ------------------------------------------------------------------

    def run(self, prompt, objective=None, max_rounds=None):
        """
        Ejecuta el sub-assistant de forma secuencial y acotada.

        Cada ronda consulta al modelo con el contexto de trabajo; si el
        modelo propone una herramienta, se ejecuta vía la compuerta y el
        resultado se retroalimenta para la siguiente ronda. Termina cuando
        el modelo responde texto final, se produce un error, o se alcanza
        max_rounds.
        """
        prompt = (prompt or "").strip()

        if not prompt:
            return {
                "success": False,
                "error": "empty_prompt",
                "response": "",
                "rounds": 0,
                "tools_used": []
            }

        limit = max_rounds or self.max_rounds

        current = prompt

        if objective:
            current = f"Objetivo: {objective}\n\n{current}"

        sub_context = self._recall_sub_context(prompt)

        rounds = 0
        tools_used = []

        while rounds < limit:
            rounds += 1

            model_result = self.provider.generate(
                current,
                sub_context,
                self.tools.list_tools()
            )

            if not isinstance(model_result, dict):
                return {
                    "success": True,
                    "response": str(model_result),
                    "rounds": rounds,
                    "tools_used": tools_used
                }

            model_type = model_result.get("type")

            if model_type == "tool_call":
                tool_call = ToolCall(
                    tool=model_result.get("tool", ""),
                    arguments=model_result.get("arguments", {}),
                    reason=model_result.get("reason", "")
                )

                tool_result = self._execute_tool_call(
                    tool_call,
                    mode="subagent"
                )

                tools_used.append(tool_call.tool)

                if tool_result is None:
                    break

                if not tool_result.get("success"):
                    return {
                        "success": False,
                        "response": (
                            f"El sub-agente no pudo ejecutar "
                            f"'{tool_call.tool}'."
                        ),
                        "rounds": rounds,
                        "tools_used": tools_used,
                        "tool": tool_call.tool,
                        "error": tool_result.get("error"),
                        "tool_result": tool_result
                    }

                current = (
                    f"{current}\n\n"
                    f"Resultado de {tool_call.tool}: "
                    f"{tool_result}\n\n"
                    f"Continúa y entrega tu respuesta final."
                )
                continue

            if model_type == "error":
                return {
                    "success": False,
                    "response": model_result.get(
                        "content",
                        "El sub-agente no pudo obtener una respuesta."
                    ),
                    "rounds": rounds,
                    "tools_used": tools_used,
                    "error": model_result.get("detail", "")
                }

            # Respuesta de texto final
            return {
                "success": True,
                "response": model_result.get("content", ""),
                "rounds": rounds,
                "tools_used": tools_used
            }

        # Se alcanzó el tope de rondas
        return {
            "success": True,
            "response": (
                "El sub-agente alcanzó el límite de rondas "
                f"({limit}) y no entregó una respuesta final."
            ),
            "rounds": rounds,
            "tools_used": tools_used,
            "truncated": True
        }
