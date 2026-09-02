from memory.router import MemoryRouter
from memory.conversations import ConversationStore
from app.reasoning import ILUReasoning
from app.providers import create_runtime_provider
from app.security import SecurityGate
from app.audit import AuditLog
from config.settings import ILUSettings
from tools import create_tool_manager, ToolCall
from tasks import TaskManager
from app.subagent import SubAgent

# ---- Bloque 8: sistema de autoridad, permisos y autonomía gobernada ----
from security.grant_store import GrantStore
from security.principal import PrincipalRegistry
from security.policy import Policy
from security.emergency import EmergencyRegistry
from security.device import DeviceRegistry
from security.authority import Authority
from security.spoofing import SpoofingGuard
from security.authorization_request import (
    AuthorizationRequestStore,
)

# Palabras vacías en español: se descartan como candidatas de búsqueda
# para no inundar la memoria. El resto de tokens (incluso cortos como
# "té" o "ia") SÍ se buscan.
_MEMORY_STOPWORDS = {
    "que", "qué", "sobre", "con", "para", "de", "el", "la", "los",
    "las", "un", "una", "unos", "unas", "y", "o", "u", "a", "en",
    "es", "son", "mi", "mis", "tu", "tus", "su", "sus", "por", "se",
    "me", "te", "lo", "del", "al", "tengo", "cuando", "como", "cómo",
    "esta", "este", "estos", "estas", "hay", "no", "si", "sí", "ya",
    "fue", "era", "más", "mas", "muy", "bien", "puedes",
}

# Detección de delegación a un sub-agente (Bloque 7).
#
# Frases explícitas de delegación (con frontera de frase) y tokens sueltos
# (con frontera de palabra, para no coincidir dentro de "encargado" o
# "delegado"). En ambos casos se exige además una tarea no trivial: un verbo
# suelto ("delega") o una petición normal ("qué hora es") NO disparan un
# sub-agente.
_SUBAGENT_PHRASES = (
    "sub-agente", "sub agente", "sub-assistant", "sub assistant",
    "encargá a", "encarga a", "encargá esto", "encarga esto",
    "manejá esto", "maneja esto", "manejalo", "manejalo",
    "investigá en paralelo", "investiga en paralelo",
    "delegá esto", "delega esto", "delegar esto", "delegar a",
)
_SUBAGENT_TOKENS = (
    "subagente", "subagentes", "subassistant",
    "delegá", "delega", "delegar", "delegación", "delegacion",
)


class ILUCore:
    """
    Núcleo central de I.L.U.

    Flujo:
    entrada
        -> memoria
        -> herramienta directa si corresponde
        -> contexto
        -> razonamiento
        -> modelo local
        -> memoria
        -> respuesta
    """

    def __init__(self):
        self.settings = ILUSettings()
        self.name = self.settings.name
        self.version = self.settings.version

        self.memory = MemoryRouter()
        # Bloque 10: historial de conversación multi-turn (contexto entre
        # mensajes de una misma sesión). Es solo contexto de lectura; la
        # autoridad y el gateo de herramientas siguen intactos.
        self.conversations = ConversationStore(
            path=self.settings.conversations_path
        )
        self.reasoning = ILUReasoning()
        # Proveedor de ejecución: con OmniRoute, envuelto para caer en
        # Ollama local si el cloud falla (Bloque 9).
        self.provider = create_runtime_provider()
        self.tools = create_tool_manager()
        self.security = SecurityGate()
        self.audit = AuditLog()
        self.tasks = TaskManager(path=self.settings.tasks_path)

        # ---- Bloque 8: autoridad, permisos, autonomía gobernada ----
        #
        # Capas (de abajo a arriba, una única puerta de enforcement):
        #
        #     PrincipalRegistry (quién es el owner/autoridades raíz)
        #     Policy           (reglas separadas del código)
        #     GrantStore       (autorizaciones explícitas persistentes)
        #     DeviceRegistry   (dispositivos autorizados, HMAC challenge)
        #     EmergencyRegistry(protocolos previamente definidos)
        #     SpoofingGuard    (detección de suplantación de identidad)
        #     Authority        (única que concede/revoca/cambia autonomía)
        #
        # y SecurityGate (la compuerta) consulta todo lo anterior SIN darle
        # jamás a I.L.U. la capacidad de autoconcederse permisos: Authority
        # no se inyecta a la inteligencia, solo auxilia al core cuando un
        # principal humano ordena una concesión/revocación.

        self.policy = Policy(path=self.settings.policy_path)
        self.principals = PrincipalRegistry(
            path=self.settings.principals_path,
            owner_id=self.settings.owner_id,
        )
        self.grant_store = GrantStore(
            path=self.settings.grants_path
        )
        self.emergency = EmergencyRegistry(
            policy=self.policy,
            path=self.settings.emergency_path,
        )
        self.devices = DeviceRegistry(
            path=self.settings.devices_path
        )
        self.auth_requests = AuthorizationRequestStore(
            path=self.settings.authreq_path
        )
        self.spoofing = SpoofingGuard(audit=self.audit)

        # Authority conoce a la compuerta para gobernar la autonomía;
        # nunca al revés (la compuerta solo decide según grants/reglas).
        self.authority = Authority(
            grant_store=self.grant_store,
            principals=self.principals,
            audit=self.audit,
            emergency=self.emergency,
            devices=self.devices,
            policy=self.policy,
            gate=self.security,
            requests=self.auth_requests,
        )

    def _next_memory_key(self):
        memories = self.memory.load_all()

        index = 1

        while f"memory_{index}" in memories:
            index += 1

        return f"memory_{index}"

    def _save_memory(
        self,
        content,
        memory_type="conversation",
        importance=5
    ):
        content = content.strip()

        if not content:
            return None

        self.memory.save(
            self._next_memory_key(),
            content,
            memory_type=memory_type,
            importance=importance
        )

        return content

    def _detect_memory_type(self, content):
        lowered = content.lower()

        if any(
            phrase in lowered
            for phrase in (
                "prefiero",
                "prefiere",
                "me gusta",
                "no me gusta",
                "mi preferencia"
            )
        ):
            return "preference", 9

        if any(
            word in lowered
            for word in (
                "proyecto",
                "sistema",
                "aplicación",
                "aplicacion",
                "i.l.u.",
                "ilu"
            )
        ):
            return "project", 9

        if any(
            word in lowered
            for word in (
                "es ",
                "son ",
                "está ",
                "esta ",
                "tiene ",
                "usa ",
                "utiliza "
            )
        ):
            return "fact", 8

        return "knowledge", 6

    def _save_explicit_memory(self, message):
        prefixes = (
            "recuerda que ",
            "recuerda ",
            "memoriza que ",
            "memoriza ",
        )

        lowered = message.lower()

        for prefix in prefixes:
            if lowered.startswith(prefix):
                content = message[len(prefix):].strip()

                if not content:
                    return None

                memory_type, importance = self._detect_memory_type(
                    content
                )

                self._save_memory(
                    content,
                    memory_type=memory_type,
                    importance=importance
                )

                return {
                    "content": content,
                    "type": memory_type,
                    "importance": importance
                }

        return None

    def _search_memory(self, message):
        lowered = message.lower()

        if not (
            "que recuerdas" in lowered
            or "qué recuerdas" in lowered
            or "busca en tu memoria" in lowered
            or "busca en la memoria" in lowered
        ):
            return None

        words = [
            word.strip("¿?¡!,.:;")
            for word in lowered.split()
            if (
                word.strip("¿?¡!,.:;") not in _MEMORY_STOPWORDS
                and len(word.strip("¿?¡!,.:;")) >= 2
            )
        ]

        results = []

        for word in words:
            found = self.memory.search(
                word,
                limit=5
            )

            for item in found:
                if item not in results:
                    results.append(item)

        return results[:10]

    def _get_context(self, message):
        """
        Recupera recuerdos relevantes para una conversación normal.
        """

        words = [
            word.strip("¿?¡!,.:;")
            for word in message.lower().split()
            if len(word.strip("¿?¡!,.:;")) >= 5
        ]

        if not words:
            return []

        results = []

        for word in words[:6]:
            found = self.memory.search(
                word,
                limit=3
            )

            for item in found:
                if item not in results:
                    results.append(item)

        return results[:5]

    def _format_memories(self, memories):
        if not memories:
            return ""

        values = []

        for item in memories:
            content = item.get("content")

            if content:
                values.append(content)

        return " | ".join(values)

    def _basic_response(self, message):
        lowered = message.lower()

        if lowered in (
            "hola",
            "hello",
            "buenas",
            "buenos dias",
            "buenas tardes"
        ):
            return (
                "Hola. I.L.U. está disponible.",
                "greeting"
            )

        if "estado" in lowered or "status" in lowered:
            return (
                "I.L.U. está operativa.",
                "status"
            )

        if (
            "quien eres" in lowered
            or "qué eres" in lowered
            or "que eres" in lowered
        ):
            return (
                "Soy I.L.U., una arquitectura de inteligencia "
                "preparada para trabajar localmente y en la nube.",
                "identity"
            )

        return (
            None,
            "general"
        )

    def _available_tools(self):
        return self.tools.list_tools()

    def _tool_capabilities(self):
        capabilities = []

        for tool in self._available_tools():
            capabilities.append({
                "name": tool["name"],
                "description": tool["description"],
                "permission": tool["permission"]
            })

        return capabilities

    # ------------------------------------------------------------------
    # Sub-agente / sub-assistant anidado (Bloque 7)
    # ------------------------------------------------------------------

    def _subagent_task(self, message):
        """
        Extrae la tarea a delegar, o None si el mensaje no es una
        delegación clara.

        Detección flexible pero conservadora:
        - frases explícitas de delegación (p. ej. "encargá a un
          subagente", "delegar esto", "manejá esto");
        - tokens de delegación con frontera de palabra ("delega",
          "subagente"), sin coincidir dentro de "encargado"/"delegado";
        - se exige una tarea no trivial (>= 12 caracteres) para no
          disparar con "delega" suelto ni con peticiones normales.
        """
        if not isinstance(message, str):
            return None

        lowered = message.lower()

        # 1) Frases explícitas (multipalabra o con guion).
        for phrase in _SUBAGENT_PHRASES:
            if phrase in lowered:
                index = lowered.find(phrase)
                task = (
                    message[:index]
                    + message[index + len(phrase):]
                ).strip(" ,:;.¿?¡!-\n")

                if len(task) >= 12:
                    return task

        # 2) Tokens de delegación con frontera de palabra.
        tokens = {
            token.strip("¿?¡!,.:;")
            for token in lowered.split()
        }

        for token in _SUBAGENT_TOKENS:
            if token in tokens:
                index = lowered.find(token)
                task = (
                    message[:index]
                    + message[index + len(token):]
                ).strip(" ,:;.¿?¡!-\n")

                if len(task) >= 12:
                    return task

        return None

    def _is_subagent_request(self, message):
        return self._subagent_task(message) is not None

    def _run_subagent(self, message):
        """
        Delega la sub-tarea a un SubAgent que comparte proveedor, toolset,
        compuerta de seguridad y auditor del padre. Nunca eleva permisos.
        """
        task = self._subagent_task(message)

        if not task:
            return {
                "success": False,
                "input": message,
                "intent": "subagent",
                "response": (
                    "I.L.U. no pudo identificar una tarea clara "
                    "para delegar a un sub-agente."
                ),
                "core": self.name,
                "version": self.version
            }

        sub = SubAgent(
            provider=self.provider,
            tools=self.tools,
            security=self.security,
            audit=self.audit,
            memory=self.memory,
            grant_store=self.grant_store,
            policy=self.policy,
            emergency=self.emergency
        )

        result = sub.run(task)

        self.audit.record(
            actor="ilu",
            action="subagent",
            success=result.get("success"),
            rounds=result.get("rounds"),
            tools_used=result.get("tools_used")
        )

        response = result.get("response")

        if not response:
            response = (
                "El sub-agente no devolvió una respuesta."
            )

        return {
            "success": result.get("success", False),
            "input": message,
            "intent": "subagent",
            "response": response,
            "subagent": {
                "rounds": result.get("rounds", 0),
                "tools_used": result.get("tools_used", []),
                "truncated": result.get("truncated", False),
                "tool": result.get("tool"),
                "error": result.get("error")
            },
            "reasoning": {
                "type": "subagent",
                "context_used": 0,
                "complexity": "delegated"
            },
            "provider": {
                "name": self.provider.name,
                "version": self.provider.version
            },
            "tools": self._tool_capabilities(),
            "core": self.name,
            "version": self.version
        }

    def _identify_tool(self, message):
        """
        Identifica herramientas simples y deterministas por frase.

        El panel de Bloque 6 añade a system_time: web_search, read_file
        y notify (todas de solo lectura/inocuas). Si la frase coincide
        pero falta el argumento imprescindible (p. ej. "busca en
        internet" sin consulta), se devuelve None y el modelo lo resuelve.
        """

        lowered = message.lower().strip()

        time_phrases = (
            "qué hora es",
            "que hora es",
            "dime la hora",
            "dime qué hora es",
            "dime que hora es",
            "hora actual",
            "hora tenemos",
            "qué hora tenemos",
            "que hora tenemos",
            "cuál es la hora",
            "cual es la hora",
            "fecha y hora",
            "fecha hora",
        )

        if any(
            phrase in lowered
            for phrase in time_phrases
        ):
            if self.tools.has_tool("system_time"):
                return "system_time"

        if (
            self.tools.has_tool("web_search")
            and self._extract_web_query(message)
        ):
            return "web_search"

        if (
            self.tools.has_tool("read_file")
            and self._extract_file_path(message)
        ):
            return "read_file"

        if (
            self.tools.has_tool("notify")
            and self._extract_notify_message(message)
        ):
            return "notify"

        return None

    @staticmethod
    def _strip_quotes(text):
        return text.strip().strip('"').strip("'").strip('"')

    def _extract_web_query(self, message):
        lowered = message.lower()

        markers = (
            "busca en internet sobre",
            "busca en la web sobre",
            "investiga en internet sobre",
            "busca en internet",
            "busca en la web",
            "investiga en internet",
        )

        for marker in markers:
            if marker in lowered:
                index = lowered.find(marker) + len(marker)
                return (
                    self._strip_quotes(message[index:])
                    .strip(":.,; ")
                )

        return ""

    def _extract_file_path(self, message):
        lowered = message.lower()

        markers = (
            "lee el archivo",
            "leé el archivo",
            "muéstrame el archivo",
            "muestra el archivo",
            "abre el archivo",
        )

        for marker in markers:
            if marker in lowered:
                index = lowered.find(marker) + len(marker)
                return (
                    self._strip_quotes(message[index:])
                    .strip(":.,; ")
                )

        return ""

    def _extract_notify_message(self, message):
        lowered = message.lower()

        markers = (
            "notifícame",
            "notificame",
            "avísame",
            "avisame",
            "déjame una nota",
            "dejame una nota",
        )

        for marker in markers:
            if marker in lowered:
                index = lowered.find(marker) + len(marker)
                return (
                    self._strip_quotes(message[index:])
                    .strip(":.,; ")
                )

        return ""

    def _tool_arguments(self, tool_name, message):
        """Argumentos deterministas para una herramienta directa."""
        if tool_name == "web_search":
            return {"query": self._extract_web_query(message)}

        if tool_name == "read_file":
            return {"path": self._extract_file_path(message)}

        if tool_name == "notify":
            return {"message": self._extract_notify_message(message)}

        return {}

    def _create_direct_tool_call(self, message):
        """
        Crea una ToolCall directamente para herramientas
        simples, deterministas y seguras.

        Solo se crea si el argumento imprescindible de la herramienta
        está presente (consulta, ruta o mensaje).
        """

        tool_name = self._identify_tool(message)

        if not tool_name:
            return None

        if not self.tools.has_tool(tool_name):
            return None

        arguments = self._tool_arguments(tool_name, message)

        required_argument = {
            "web_search": "query",
            "read_file": "path",
            "notify": "message",
        }.get(tool_name)

        if required_argument:
            value = arguments.get(required_argument, "")

            if not str(value).strip():
                return None

        return ToolCall(
            tool=tool_name,
            arguments=arguments,
            reason=(
                "Herramienta identificada directamente "
                "por I.L.U."
            )
        )

    def _task_command(self, message):
        """
        Comandos de tareas por lenguaje natural.

        I.L.U. puede crear y consultar tareas directamente, sin pasar
        por el LLM. La EJECUCIÓN de trabajos en segundo plano queda en
        el servidor (catálogo de callables registrados); aquí solo se
        gestiona su registro y estado.
        """
        lowered = message.lower().strip()

        create_prefixes = (
            "crea una tarea",
            "crea la tarea",
            "registra una tarea",
            "nueva tarea",
            "crea tarea",
        )

        for prefix in create_prefixes:
            if lowered.startswith(prefix):
                title = (
                    message[len(prefix):].strip().lstrip(": ,.;-")
                )

                if not title:
                    return {
                        "success": True,
                        "input": message,
                        "intent": "task_create_pending",
                        "response": (
                            "¿Qué tarea quieres que registre? "
                            "Ejemplo: crea una tarea: "
                            "revisar los informes."
                        ),
                        "core": self.name,
                        "version": self.version
                    }

                task = self.tasks.create(title=title)

                self.audit.record(
                    actor="ilu",
                    action="task_create",
                    task_id=task["id"],
                    title=title
                )

                return {
                    "success": True,
                    "input": message,
                    "intent": "task_create",
                    "response": (
                        f"Tarea '{task['title']}' creada "
                        f"(ID: {task['id']})."
                    ),
                    "task_id": task["id"],
                    "core": self.name,
                    "version": self.version
                }

        list_phrases = (
            "qué tareas",
            "que tareas",
            "lista de tareas",
            "listar tareas",
            "mis tareas",
            "estado de las tareas",
            "estado de mis tareas",
        )

        if any(
            phrase in lowered
            for phrase in list_phrases
        ):
            tasks = self.tasks.list_tasks()[:10]

            if not tasks:
                return {
                    "success": True,
                    "input": message,
                    "intent": "task_list",
                    "response": (
                        "No hay tareas registradas."
                    ),
                    "tasks": [],
                    "core": self.name,
                    "version": self.version
                }

            lines = [
                f"{task['title']} — {task['state']} "
                f"({task['progress']}%)"
                for task in tasks
            ]

            return {
                "success": True,
                "input": message,
                "intent": "task_list",
                "response": (
                    f"{len(tasks)} tareas: "
                    + " | ".join(lines)
                ),
                "tasks": [
                    {
                        "id": task["id"],
                        "title": task["title"],
                        "state": task["state"],
                        "progress": task["progress"]
                    }
                    for task in tasks
                ],
                "core": self.name,
                "version": self.version
            }

        status_phrases = (
            "estado de la tarea",
            "cómo va la tarea",
            "como va la tarea",
            "progreso de la tarea",
            "cómo van las tareas",
            "como van las tareas",
            "progreso de mis tareas",
        )

        if any(
            phrase in lowered
            for phrase in status_phrases
        ):
            latest = self.tasks.list_tasks()

            if not latest:
                return {
                    "success": True,
                    "input": message,
                    "intent": "task_status",
                    "response": "No hay tareas todavía.",
                    "core": self.name,
                    "version": self.version
                }

            task = latest[0]

            return {
                "success": True,
                "input": message,
                "intent": "task_status",
                "response": (
                    f"'{task['title']}' — estado "
                    f"{task['state']}, progreso "
                    f"{task['progress']}%."
                ),
                "task_id": task["id"],
                "state": task["state"],
                "progress": task["progress"],
                "core": self.name,
                "version": self.version
            }

        return None

    def _memory_reply(self, response, intent, **extra):
        payload = {
            "success": True,
            "input": None,
            "intent": intent,
            "response": response,
            "core": self.name,
            "version": self.version
        }

        payload.update(extra)
        return payload

    def _memory_command(self, message):
        """
        Administración de memoria por lenguaje natural.

        I.L.U. puede olvidar, corregir información antigua y consultar
        distintos tipos de memoria directamente, sin pasar por el LLM.
        La EJECUCIÓN queda limitada a la memoria (nada peligroso); el
        resto del pipeline sigue intacto.
        """
        lowered = message.lower().strip()

        # --------------------------------------------------------------
        # OLVIDAR
        # --------------------------------------------------------------
        forget_prefixes = (
            "olvida que ",
            "olvida ",
            "borra de tu memoria ",
            "olvida la memoria de ",
        )

        for prefix in forget_prefixes:
            if lowered.startswith(prefix):
                target = message[len(prefix):].strip()

                if not target:
                    return None

                deleted = self.memory.forget(target)

                if deleted:
                    return self._memory_reply(
                        f"Olvidado: {deleted.content}",
                        "memory_forget"
                    )

                return self._memory_reply(
                    "No encontré ese recuerdo para olvidar.",
                    "memory_forget"
                )

        # --------------------------------------------------------------
        # CORREGIR información antigua
        # --------------------------------------------------------------
        correction_prefixes = (
            "corrige que ",
            "actualiza que ",
            "corrige: ",
        )

        for prefix in correction_prefixes:
            if lowered.startswith(prefix):
                rest = message[len(prefix):].strip()

                if " por " not in rest:
                    return None

                old, new = rest.split(" por ", 1)

                updated = self.memory.correct(
                    old.strip(),
                    new.strip()
                )

                if updated:
                    return self._memory_reply(
                        f"Corregido. Ahora recuerdo: "
                        f"{updated.content}",
                        "memory_update"
                    )

                return self._memory_reply(
                    "No encontré qué corregir.",
                    "memory_update"
                )

        # --------------------------------------------------------------
        # CONSULTAR por tipo
        # --------------------------------------------------------------
        if (
            "habilidades" in lowered
            or "qué sabes hacer" in lowered
            or "que sabes hacer" in lowered
        ):
            skills = self.memory.list_by_type(
                "skill",
                limit=20
            )

            if not skills:
                return self._memory_reply(
                    "Aún no he registrado habilidades.",
                    "memory_read"
                )

            names = [skill.content for skill in skills]

            return self._memory_reply(
                "Mis habilidades registradas: " + " | ".join(names),
                "memory_read",
                memory_count=len(names)
            )

        if (
            "cuántos recuerdos" in lowered
            or "cuántas memorias" in lowered
            or "cuantas memorias" in lowered
        ):
            stats = self.memory.stats()

            if stats["total"] == 0:
                return self._memory_reply(
                    "No tengo recuerdos guardados todavía.",
                    "memory_read",
                    memory_count=0
                )

            counts = ", ".join(
                f"{name}={count}"
                for name, count in sorted(
                    stats["counts"].items()
                )
            )

            return self._memory_reply(
                f"Tengo {stats['total']} recuerdos: {counts}.",
                "memory_read",
                memory_count=stats["total"]
            )

        return None

    # ------------------------------------------------------------------
    # Autoridad / permisos por lenguaje natural (Bloque 8)
    #
    # I.L.U. jamás decide por sí misma qué permisos existen: solo actúa
    # como interfaz de voz/texto hacia Authority, y Authority exige un
    # principal raíz (el OWNER) para cualquier concesión/revocación.
    # El nivel concedido es SIEMPRE "execution" (nunca se autoconcede ni
    # se delega autoridad por lenguaje natural).
    # ------------------------------------------------------------------

    def _authority_command(self, message):
        lowered = message.lower().strip()

        # ----------------------------------------------------------
        # ESTADO DE PERMISOS
        # ----------------------------------------------------------
        status_phrases = (
            "estado de permisos",
            "estado de los permisos",
            "qué permisos hay",
            "que permisos hay",
            "qué permisos tiene",
            "que permisos tiene",
            "lista de permisos",
            "permitidos",
        )

        if any(phrase in lowered for phrase in status_phrases):
            grants = self.grant_store.list(limit=50)

            if not grants:
                return self._authority_reply(
                    message,
                    "No hay permisos otorgados todavía.",
                    "permission_status",
                    grants=[],
                )

            grant_dicts = [
                grant.to_dict() for grant in grants
            ]

            lines = [
                f"{g['capability']} — {g['status']} → {g['grantee']}"
                for g in grant_dicts
            ]

            return self._authority_reply(
                message,
                "Permisos: " + " | ".join(lines[:10]),
                "permission_status",
                grants=[
                    {
                        "grant_id": g["grant_id"],
                        "capability": g["capability"],
                        "status": g["status"],
                        "grantee": g["grantee"],
                        "level": g["level"],
                        "expires_at": g["expires_at"],
                    }
                    for g in grant_dicts
                ],
            )

        # ----------------------------------------------------------
        # CONCEDER permiso (solo owner; nivel execution)
        # ----------------------------------------------------------
        grant_prefixes = (
            "autoriza ",
            "autorizá ",
            "concede permiso para ",
            "concedé permiso para ",
            "da permiso para ",
            "dá permiso para ",
        )

        for prefix in grant_prefixes:
            if lowered.startswith(prefix):
                target = message[len(prefix):].strip().strip(".")
                target = target.strip()

                if not target:
                    return None

                capability = target.split()[-1].strip(".,;:").lower()

                # Solo capacidades de ejecución; jamás autoridad,
                # modificación de política ni autoconcesión.
                if self.policy.is_prohibited(capability):
                    return self._authority_reply(
                        message,
                        "No puedo otorgar un permiso para una acción "
                        "prohibida por policy.",
                        "permission_error",
                        error="capability_prohibited",
                    )

                try:
                    grant = self.authority.grant(
                        capability=capability,
                        actor=self.settings.owner_id,
                        reason="solicitado por owner por lenguaje natural",
                        origin="nl_owner_command",
                    )
                except PermissionError:
                    return self._authority_reply(
                        message,
                        "Esta orden exige autoridad raíz (owner).",
                        "permission_error",
                        error="no_autoridad_raiz",
                    )
                except ValueError as error:
                    return self._authority_reply(
                        message,
                        "No pude conceder ese permiso: "
                        f"{error}.",
                        "permission_error",
                        error=str(error),
                    )

                self._save_memory(
                    message,
                    memory_type="conversation",
                    importance=3,
                )

                return self._authority_reply(
                    message,
                    f"Permiso otorgado: '{capability}' (ID {grant.key}).",
                    "permission_granted",
                    grant={
                        "grant_id": grant.key,
                        "capability": grant.capability,
                        "level": grant.level,
                        "expires_at": grant.expires_at,
                    },
                )

        # ----------------------------------------------------------
        # REVOCAR permiso (solo owner)
        # ----------------------------------------------------------
        revoke_prefixes = (
            "revoca el permiso para ",
            "revocá el permiso para ",
            "revoca permiso para ",
            "revoca ",
            "revocá ",
        )

        for prefix in revoke_prefixes:
            if lowered.startswith(prefix):
                target = message[len(prefix):].strip().strip(".")
                target = target.strip()

                if not target:
                    return None

                target = target.split()[-1].strip(".,;:").lower()

                # ¿Target es grant_id directo o una capacidad?
                grant = self.grant_store.get(target)

                if grant is None:
                    matches = self.grant_store.list(
                        capability=target,
                        status="active",
                    )

                    if matches:
                        grant = matches[0]

                if grant is None:
                    return self._authority_reply(
                        message,
                        f"No hay un permiso activo para '{target}'.",
                        "permission_revoked",
                        grant=None,
                    )

                try:
                    revoked = self.authority.revoke(
                        grant.key,
                        actor=self.settings.owner_id,
                        reason="revocado por owner por lenguaje natural",
                    )
                except PermissionError:
                    return self._authority_reply(
                        message,
                        "Esta orden exige autoridad raíz (owner).",
                        "permission_error",
                        error="no_autoridad_raiz",
                    )

                if revoked is None:
                    return self._authority_reply(
                        message,
                        f"No hay un permiso activo para '{target}'.",
                        "permission_revoked",
                        grant=None,
                    )

                self._save_memory(
                    message,
                    memory_type="conversation",
                    importance=3,
                )

                return self._authority_reply(
                    message,
                    f"Permiso revocado: '{target}'.",
                    "permission_revoked",
                    grant={
                        "grant_id": revoked.key,
                        "capability": revoked.capability,
                        "status": revoked.status,
                    },
                )

        # ----------------------------------------------------------
        # CAMBIAR NIVEL DE AUTONOMÍA (solo owner)
        # ----------------------------------------------------------
        autonomy_prefixes = (
            "cambia la autonomía a ",
            "cambia la autonomia a ",
            "poné la autonomía en ",
            "pon la autonomía en ",
            "pone la autonomia en ",
            "autonomía a ",
            "autonomia a ",
        )

        for prefix in autonomy_prefixes:
            if lowered.startswith(prefix):
                level = message[len(prefix):].strip().lower().strip(".")

                # Acepta español e inglés para los tres niveles; evita
                # que "autónoma/o" se rechace por el nombre en inglés.
                level = {
                    "manual": "manual",
                    "asistido": "assisted",
                    "asistida": "assisted",
                    "assisted": "assisted",
                    "autónomo": "autonomous",
                    "autónoma": "autonomous",
                    "autonomo": "autonomous",
                    "autonoma": "autonomous",
                    "autonomous": "autonomous",
                }.get(level, level)

                if level not in self.security.AUTONOMY_LEVELS:
                    return self._authority_reply(
                        message,
                        "Nivel de autonomía inválido. Válidos: "
                        + ", ".join(self.security.AUTONOMY_LEVELS)
                        + ".",
                        "permission_error",
                        error="invalid_autonomy_level",
                    )

                try:
                    change = self.authority.set_autonomy(
                        level,
                        actor=self.settings.owner_id,
                    )
                except PermissionError:
                    return self._authority_reply(
                        message,
                        "Solo el owner puede cambiar la autonomía.",
                        "permission_error",
                        error="no_autoridad_raiz",
                    )

                self._save_memory(
                    message,
                    memory_type="conversation",
                    importance=3,
                )

                return self._authority_reply(
                    message,
                    f"Autonomía: {change['from']} → {change['to']}.",
                    "autonomy_change",
                    autonomy=change,
                )

        return None

    def _authority_reply(self, message, response, intent, **extra):
        payload = {
            "success": True,
            "input": message,
            "intent": intent,
            "response": response,
            "core": self.name,
            "version": self.version,
        }

        payload.update(extra)
        return payload

    def _execute_tool_call(self, tool_call, mode="direct"):
        """
        Ejecuta una ToolCall a través de la compuerta de autorización.

        Una respuesta del modelo NUNCA equivale a permiso de ejecución:
        toda herramienta pasa primero por SecurityGate y se audita.
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

        decision = self.security.decide(
            tool_call.tool,
            permission,
            mode=mode,
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
            reason=decision["reason"],
            grant_id=decision.get("grant_id")
        )

        if decision["decision"] != "allow":
            result = {
                "success": False,
                "error": decision["reason"],
                "tool": tool_call.tool,
                "authorization": decision["decision"]
            }

            # La compuerta pidió autorización humana: se abre una
            # SOLICITUD de autorización (persistente, auditable). El
            # owner podrá concederla/denegarla; en modo manual las
            # propuestas del modelo NO generan solicitud (no es "falta
            # de permiso", es delegación deliberada de la decisión).
            if (
                decision["decision"] == "ask"
                and decision.get("reason") == "authorization_required"
            ):
                request = self.auth_requests.open(
                    capability=tool_call.tool,
                    reason="Necesita autorización para {tool}".format(
                        tool=tool_call.tool
                    ),
                    principal=self.settings.owner_id,
                    scope={
                        "type": "tool",
                        "tool": tool_call.tool,
                    },
                )
                result["request_id"] = request.key

            return result

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

    def _build_tool_response(
        self,
        message,
        tool_call,
        tool_result,
        source="direct_tool"
    ):
        if not tool_result:
            return None

        if not tool_result.get("success"):
            tool_name = (
                tool_call.tool
                if tool_call
                else "?"
            )

            if (
                tool_result.get("authorization")
                == "ask"
            ):
                reason = tool_result.get(
                    "error",
                    ""
                )

                if reason == "manual_mode_proposal":
                    response = (
                        f"I.L.U. está en modo manual y no ejecuta "
                        f"la herramienta '{tool_name}' por su cuenta."
                    )
                else:
                    response = (
                        f"I.L.U. necesita autorización humana para "
                        f"ejecutar la herramienta '{tool_name}'."
                    )

                    request_id = tool_result.get("request_id")

                    if request_id:
                        response += (
                            f" Solicitud abierta: {request_id}."
                        )
            else:
                response = (
                    "I.L.U. no pudo ejecutar "
                    "la herramienta."
                )

            return {
                "success": False,
                "input": message,
                "intent": "tool_error",
                "response": response,
                "context": "",
                "reasoning": {
                    "type": source,
                    "context_used": 0
                },
                "provider": {
                    "name": self.provider.name,
                    "version": self.provider.version
                },
                "tools": self._tool_capabilities(),
                "tool": (
                    tool_call.tool
                    if tool_call
                    else None
                ),
                "tool_call": (
                    tool_call.to_dict()
                    if tool_call
                    else None
                ),
                "tool_result": tool_result,
                "authorization": tool_result.get(
                    "authorization"
                ),
                "authorization_request_id": tool_result.get(
                    "request_id"
                ),
                "core": self.name,
                "version": self.version
            }

        result = tool_result.get("result", {})

        if (
            isinstance(result, dict)
            and "datetime" in result
        ):
            response = (
                "La fecha y hora de tu sistema es: "
                f"{result['datetime']}"
            )
        else:
            response = str(result)

        self._save_memory(
            message,
            memory_type="conversation",
            importance=3
        )

        return {
            "success": True,
            "input": message,
            "intent": "tool_use",
            "response": response,
            "context": "",
            "reasoning": {
                "type": source,
                "context_used": 0
            },
            "provider": {
                "name": self.provider.name,
                "version": self.provider.version
            },
            "tools": self._tool_capabilities(),
            "tool": tool_result.get("tool"),
            "tool_call": (
                tool_call.to_dict()
                if tool_call
                else None
            ),
            "tool_result": result,
            "core": self.name,
            "version": self.version
        }

    def process(self, message, session_id=None):
        if not isinstance(message, str):
            return {
                "success": False,
                "error": "message_must_be_text"
            }

        message = message.strip()

        if not message:
            return {
                "success": False,
                "error": "empty_message"
            }

        # Bloque 10: cada conversación vive bajo un session_id. Si no se
        # indica, se usa la sesión por defecto (comportamiento heredado).
        session_id = session_id or "default"

        # ==========================================================
        # 0.5 ADMINISTRACIÓN DE MEMORIA
        #
        # I.L.U. gestiona su propia memoria por lenguaje natural:
        # olvidar, corregir información antigua y consultar por tipo.
        # Toda acción aquí es sobre memoria (nada peligroso) y no
        # requiere autorización de herramientas.
        # ==========================================================

        memory_command = self._memory_command(
            message
        )

        if memory_command is not None:
            memory_command["input"] = message
            return memory_command

        # ==========================================================
        # 0.6 AUTORIDAD / PERMISOS (solo owner)
        #
        # I.L.U. gestiona el sistema de permisos SOLO como interfaz del
        # owner hacia Authority: conceder, revocar y consultar.
        # Authority sigue siendo la única capa que emite/revoca grants
        # y subir la autonomía exige principal raíz.
        # ==========================================================

        authority_command = self._authority_command(
            message
        )

        if authority_command is not None:
            return authority_command

        # ==========================================================
        # 1. MEMORIA EXPLÍCITA
        # ==========================================================

        explicit_memory = self._save_explicit_memory(
            message
        )

        if explicit_memory:
            return {
                "success": True,
                "input": message,
                "intent": "memory_save",
                "memory_type": explicit_memory["type"],
                "importance": explicit_memory["importance"],
                "response": (
                    f"Recordado: "
                    f"{explicit_memory['content']}"
                ),
                "core": self.name,
                "version": self.version
            }

        # ==========================================================
        # 2. BÚSQUEDA EXPLÍCITA DE MEMORIA
        # ==========================================================

        memory_results = self._search_memory(
            message
        )

        if memory_results is not None:
            if not memory_results:
                response = (
                    "No encontré recuerdos relacionados."
                )
            else:
                response = (
                    "Recuerdo: "
                    + self._format_memories(
                        memory_results
                    )
                )

            return {
                "success": True,
                "input": message,
                "intent": "memory_read",
                "response": response,
                "memory_count": len(
                    memory_results
                ),
                "core": self.name,
                "version": self.version
            }

        # ==========================================================
        # 2.5 TAREAS (lenguaje natural)
        #
        # I.L.U. gestiona su registro de tareas sin depender del LLM:
        # crear, listar y consultar estado. La ejecución en segundo
        # plano queda registrada en el servidor.
        # ==========================================================

        task_command = self._task_command(
            message
        )

        if task_command is not None:
            return task_command

        # ==========================================================
        # 2.75 SUB-AGENTE / SUB-ASSISTANT ANIDADO
        #
        # Si el mensaje expresa una intención clara de delegar una
        # sub-tarea, I.L.U. la encarga a un SubAgent que comparte el
        # proveedor, el toolset y la compuerta de seguridad del padre.
        # El sub-agente nunca eleva permisos.
        # ==========================================================

        if self._is_subagent_request(message):
            return self._run_subagent(message)

        # ==========================================================
        # 3. HERRAMIENTA DIRECTA
        #
        # Las herramientas simples, seguras y deterministas
        # se ejecutan sin llamar a Ollama.
        # ==========================================================

        direct_tool_call = (
            self._create_direct_tool_call(
                message
            )
        )

        if direct_tool_call:
            direct_tool_result = (
                self._execute_tool_call(
                    direct_tool_call
                )
            )

            return self._build_tool_response(
                message,
                direct_tool_call,
                direct_tool_result
            )

        # ==========================================================
        # 4. CONTEXTO
        # ==========================================================

        context = self._get_context(
            message
        )

        # ==========================================================
        # 5. RAZONAMIENTO
        # ==========================================================

        analysis = self.reasoning.analyze(
            message,
            context
        )

        if not analysis.get("success"):
            return analysis

        reasoning = self.reasoning.respond(
            analysis
        )

        # ==========================================================
        # 6. RESPUESTA BÁSICA
        # ==========================================================

        basic_response, intent = (
            self._basic_response(
                message
            )
        )

        if basic_response:
            response = basic_response

            self._save_memory(
                message,
                memory_type="conversation",
                importance=3
            )

            return {
                "success": True,
                "input": message,
                "intent": intent,
                "response": response,
                "context": self._format_memories(
                    context
                ),
                "reasoning": {
                    "type": reasoning.get(
                        "reasoning_type"
                    ),
                    "context_used": reasoning.get(
                        "context_used",
                        0
                    ),
                    "complexity": reasoning.get(
                        "complexity",
                        "simple"
                    )
                },
                "provider": {
                    "name": self.provider.name,
                    "version": self.provider.version
                },
                "tools": self._tool_capabilities(),
                "tool": None,
                "tool_call": None,
                "tool_result": None,
                "core": self.name,
                "version": self.version
            }

        # ==========================================================
        # 7. MODELO + HERRAMIENTAS
        #
        # I.L.U. entrega al modelo la lista de herramientas
        # registradas y permitidas para que el modelo pueda
        # solicitarlas cuando la tarea lo necesite.
        #
        # La ejecución se restringe al registro de herramientas:
        # solo se ejecutan herramientas registradas y no
        # bloqueadas (ToolManager). Si el modelo propone una
        # herramienta desconocida, la petición se rechaza de
        # forma honesta y no se ejecuta nada.
        # ==========================================================

        # Bloque 10: se inyecta el historial de la sesión como contexto
        # adicional para que el modelo recuerde lo dicho antes. Es solo
        # contexto de lectura; no cambia el gateo de herramientas.
        model_context = list(context or [])

        history_turns = self.conversations.recent(
            session_id,
            limit=self.settings.history_turns
        )

        if history_turns:
            model_context.append({
                "content": (
                    self.conversations.transcript(history_turns)
                )
            })

        # Bloque 10: se registra el turno del usuario antes de la llamada.
        self.conversations.append(
            session_id,
            "user",
            message
        )

        model_result = self.provider.generate(
            message,
            model_context,
            self._available_tools()
        )

        if isinstance(model_result, dict):
            model_type = model_result.get(
                "type",
                "text"
            )

            if model_type == "tool_call":
                tool_call = ToolCall(
                    tool=model_result.get(
                        "tool",
                        ""
                    ),
                    arguments=model_result.get(
                        "arguments",
                        {}
                    ),
                    reason=model_result.get(
                        "reason",
                        ""
                    )
                )

                tool_result = self._execute_tool_call(
                    tool_call,
                    mode="model"
                )

                tool_response = self._build_tool_response(
                    message,
                    tool_call,
                    tool_result,
                    source="model_tool"
                )

                if tool_response:
                    return tool_response

            if model_type == "error":
                response = model_result.get(
                    "content",
                    "I.L.U. no pudo obtener una respuesta."
                )

            else:
                response = model_result.get(
                    "content",
                    "I.L.U. no recibió una respuesta válida."
                )

        else:
            response = str(model_result)

        # Bloque 10: se registra el turno del asistente para que la
        # siguiente consulta de la sesión tenga contexto de lo respondido.
        self.conversations.append(
            session_id,
            "assistant",
            response
        )

        # ==========================================================
        # 8. MEMORIA DE CONVERSACIÓN
        # ==========================================================

        self._save_memory(
            message,
            memory_type="conversation",
            importance=3
        )

        # Bloque 9: cuál motor respondió (primario o, si se hizo fallback,
        # el respaldo local). Solo aplica cuando el resultado vino del
        # modelo; en el resto de caminos es el proveedor por defecto.
        provider_used = (
            model_result.get("provider_used")
            if isinstance(model_result, dict)
            else None
        )

        provider_version = (
            model_result.get("provider_used_version")
            if isinstance(model_result, dict)
            else None
        )

        provider_meta = {
            "name": provider_used or self.provider.name,
            "version": provider_version or self.provider.version,
        }

        if isinstance(model_result, dict) and model_result.get("fallback"):
            provider_meta["fallback"] = True

        return {
            "success": True,
            "input": message,
            "intent": intent,
            "response": response,
            "context": self._format_memories(
                context
            ),
            "reasoning": {
                "type": reasoning.get(
                    "reasoning_type"
                ),
                "context_used": reasoning.get(
                    "context_used",
                    0
                ),
                "complexity": reasoning.get(
                    "complexity",
                    "simple"
                )
            },
            "provider": provider_meta,
            "tools": self._tool_capabilities(),
            "tool": None,
            "tool_call": None,
            "tool_result": None,
            "core": self.name,
            "version": self.version
        }
