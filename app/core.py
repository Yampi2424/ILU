import re

from memory.router import MemoryRouter
from memory.conversations import ConversationStore
from app.reasoning import ILUReasoning
from app import toolshape
from app.providers import create_runtime_provider
from app.security import SecurityGate
from app.audit import AuditLog
from app.planning import GoalPlanner
from app.learning import LearningEngine
from app.identity_recognition import IdentityRecognizer
from app.proactivity import ProactivityEngine
from app.perception import create_perception_hub
from app.integrations import IntegrationManager
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
from security.owner_secret import OwnerSecret

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
        # Bloque 14: desde el primer arranque, I.L.U. conoce a su creador
        # (memoria durable, idempotente). Se hace al inicio porque el resto
        # del wiring no depende de ello.
        self._bootstrap_creator_identity()
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

        # ---- JARVIS Evolution: planificación, aprendizaje, identidad,
        #      proactividad, percepción e integración de dispositivos.
        #      Ninguno de estos módulos otorga permisos por sí mismo:
        #      la autoridad sigue viviendo en Authority/SecurityGate.
        self.planner = GoalPlanner(
            path=self.settings.goals_path,
            task_manager=self.tasks,
        )
        self.learning = LearningEngine(memory=self.memory)
        self.recognizer = IdentityRecognizer()
        self.proactivity = ProactivityEngine(
            path=self.settings.proactivity_path
        )
        self.perception = create_perception_hub()
        self.integrations = IntegrationManager(
            security=self.security,
            audit=self.audit,
            grant_store=None,   # se inyecta abajo tras crear el store
        )

        self.policy = Policy(path=self.settings.policy_path)
        self.principals = PrincipalRegistry(
            path=self.settings.principals_path,
            owner_id=self.settings.owner_id,
        )
        self.grant_store = GrantStore(
            path=self.settings.grants_path
        )
        # Conexiones que dependen de objetos creados después del planner:
        # el reconocedor conoce a los principales y las integraciones
        # consultan el grant_store real para autorizar.
        self.recognizer.principals = self.principals
        self.integrations.grant_store = self.grant_store
        # Bloque 13: las tools de ejecución real (run_command / open_app /
        # media_control) se registran DESPUÉS de conectar integrations con
        # el grant_store: sus handlers delegan con pre_authorized=True.
        # create_tool_manager() (5 tools) queda intacto.
        self._register_world_tools()
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
        # Bloque 14: la clave de autorización del owner (PIN), leída por
        # defecto del archivo local security/owner.pin (gitignored) o de
        # la variable de entorno ILU_OWNER_SECRET. La consulta es perezosa,
        # así que configurar la clave después de construir el core funciona.
        self.owner_secret = OwnerSecret(
            path=self.settings.owner_secret_path,
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

    def _bootstrap_creator_identity(self):
        """Bloque 14 — I.L.U. conoce a su creador desde el primer arranque.

        Persiste una memoria DURABLE (tipo "family", importancia máxima)
        con el nombre real del creador. Idempotente: usa una clave fija y
        la store la hace upsert por (memory_type, memory_key), así que
        repetir el arranque no crea duplicados.
        """
        from config.identity import ILU_IDENTITY

        creator = ILU_IDENTITY.get("creator")

        if not creator:
            return

        self.memory.save(
            key="creador",
            value=creator,
            memory_type="family",
            importance=10,
        )

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

        Primero hace recall SEMÁNTICO del mensaje completo (por significado,
        no solo por palabras); después el recall léxico por palabra como
        refuerzo. Se deduplican por clave y se acota el resultado.
        """

        words = [
            word.strip("¿?¡!,.:;")
            for word in message.lower().split()
            if len(word.strip("¿?¡!,.:;")) >= 5
        ]

        results = []
        seen = set()

        # 1) Recall semántico: recuerdos afines al significado del mensaje,
        #    aunque no compartan palabras exactas.
        if message.strip():
            try:
                for item in self.memory.semantic_search(message, limit=5):
                    key = item.get("key")
                    if key and key in seen:
                        continue
                    if key:
                        seen.add(key)
                    results.append(item)
            except Exception:
                pass

        # 2) Recall léxico por palabra (comportamiento previo, refuerzo).
        for word in words[:6]:
            found = self.memory.search(word, limit=3)

            for item in found:
                key = item.get("key")
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
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

    # ------------------------------------------------------------------
    # Conciencia unificada (orquestación JARVIS)
    #
    # I.L.U. NO es una colección de módulos: cada turno se resuelve como
    # una sola inteligencia que sabe quién es, con quién habla, qué ha
    # aprendido, qué objetivos tiene, qué percibe y qué tiene pendiente.
    # Este bloque construye ese estado unificado (awareness) y lo inyecta
    # al modelo y a la respuesta, para que percibir→comprender→recordar→
    # razonar→planificar→decidir→ejecutar→verificar→aprender sean UN
    # mismo flujo.
    # ------------------------------------------------------------------

    def _build_awareness(self, message, session_id="default"):
        """
        Estado unificado de conciencia de I.L.U. en este turno.

        Todo es SOLO lectura del dominio propio de I.L.U. (memoria,
        identidad, objetivos, percepción, proactividad): jamás ejecuta
        acciones ni concede permisos. Cada sub-consulta va aislada en
        try/except para que un fallo de un módulo no rompa el resto.
        """
        awareness = {
            "self": self.name,
            "version": self.version,
        }

        # 1) Identidad: quién es I.L.U. y con quién habla.
        try:
            recognition = self.recognizer.recognize(message)
            awareness["identity"] = {
                "user": recognition.get("principal_id"),
                "user_kind": recognition.get("kind"),
                "method": recognition.get("method"),
            }
        except Exception:
            awareness["identity"] = {"user": None, "user_kind": None}

        # 2) Aprendizaje / personalización.
        try:
            profile = self.learning.profile()
            awareness["profile_count"] = profile["count"]
            awareness["preferences"] = [
                item["content"]
                for item in profile["groups"].get("preference", [])
            ][:5]
            awareness["personal"] = [
                item["content"]
                for item in profile["groups"].get("personal", [])
            ][:5]
        except Exception:
            awareness["profile_count"] = 0
            awareness["preferences"] = []
            awareness["personal"] = []

        # 3) Objetivos activos y su progreso.
        try:
            goals = self.planner.list(status="active")
            awareness["goals"] = [
                {
                    "title": goal["title"],
                    "progress": self.planner.progress(goal["id"])["percent"],
                    "status": goal["status"],
                }
                for goal in goals[:5]
            ]
        except Exception:
            awareness["goals"] = []

        # 4) Percepción: solo los sensores realmente disponibles, con un
        #    resumen del dato real sensado (no solo el nombre).
        try:
            awareness["perception"] = [
                {
                    "capability": cap["capability"],
                    "summary": self._perception_snapshot(cap["capability"]),
                }
                for cap in self.perception.list_capabilities()
                if cap["available"]
            ]
        except Exception:
            awareness["perception"] = []

        # 5) Proactividad: reglas vencidas en este instante (no se marcan
        #    como disparadas aquí; eso lo hace el orquestador proactivo).
        try:
            awareness["proactive"] = [
                {
                    "id": rule["id"],
                    "kind": rule.get("kind"),
                    "text": rule.get("text"),
                }
                for rule in self.proactivity.due_now(limit=5)
            ]
        except Exception:
            awareness["proactive"] = []

        return awareness

    def _perception_snapshot(self, capability):
        """
        Compacta el dato REAL de un sensor disponible a una línea legible
        para el prompt del modelo y la etiqueta de presencia. Nunca lanza:
        la percepción es best-effort (el entorno puede cambiar).
        """
        try:
            result = self.perception.perceive(capability)
            if not result.get("available"):
                return None
            data = result.get("data") or {}

            if capability == "network":
                gateway = (data.get("gateway") or {}).get("gateway")
                text = "red " + str(data.get("connectivity", "?"))
                if gateway:
                    text += " gw " + gateway
                ifaces = len(data.get("interfaces", {}))
                if ifaces:
                    text += f" ({ifaces} ifaces)"
                return text

            if capability == "proximity":
                return (
                    f"{data.get('human_count', 0)} humano(s), "
                    f"{data.get('lan_device_count', 0)} dispositivos LAN"
                )

            if capability == "audio":
                return f"{len(data.get('microphones', []))} micrófono(s)"

            if capability == "camera":
                return f"{len(data.get('cameras', []))} cámara(s)"

            if capability == "system_state":
                return f"uptime {data.get('uptime_seconds', 0)}s"

            if capability == "filesystem":
                return f"{data.get('count', 0)} archivos"

            return capability
        except Exception:
            return None

    def _awareness_context(self, awareness):
        """
        Aplana el awareness a bloques etiquetados para el prompt del
        modelo (y para la respuesta). Cada bloque lleva su "role" para
        que el modelo lo use con el peso correcto.
        """
        items = []

        if awareness.get("identity") and awareness["identity"].get("user"):
            items.append({
                "role": "usuario reconocido",
                "content": awareness["identity"]["user"],
            })

        if awareness.get("preferences"):
            items.append({
                "role": "preferencias del usuario",
                "content": " | ".join(awareness["preferences"]),
            })

        if awareness.get("personal"):
            items.append({
                "role": "sobre el usuario",
                "content": " | ".join(awareness["personal"]),
            })

        if awareness.get("goals"):
            items.append({
                "role": "objetivos activos",
                "content": " | ".join(
                    f"{goal['title']} ({goal['progress']}%)"
                    for goal in awareness["goals"]
                ),
            })

        if awareness.get("perception"):
            items.append({
                "role": "percepción disponible",
                "content": " | ".join(
                    f"{sensor['capability']}: {sensor.get('summary') or 'disponible'}"
                    for sensor in awareness["perception"]
                ),
            })

        if awareness.get("proactive"):
            items.append({
                "role": "pendientes proactivos",
                "content": " | ".join(
                    f"[{item['kind']}] {item['text']}"
                    for item in awareness["proactive"]
                ),
            })

        return items

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
        # Bloque 11: lista completa (con JSON-schema) para que el proveedor
        # reciba los `parameters` reales. `list_tools()` (público,
        # retrocompatible) no cambia.
        return self.tools.list_tools_full()

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
            emergency=self.emergency,
            spoofing=self.spoofing
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

        # Bloque 13: ejecución real sobre el mundo. Estas frases requieren
        # grant (permission="ask"); el despacho directo reduce latencia,
        # pero la compuerta decide igual que en el camino del modelo.
        if (
            self.tools.has_tool("run_command")
            and self._extract_run_command(message)
        ):
            return "run_command"

        if (
            self.tools.has_tool("open_app")
            and self._extract_open_app(message)
        ):
            return "open_app"

        if (
            self.tools.has_tool("media_control")
            and self._extract_media_action(message)
        ):
            return "media_control"

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

    def _register_world_tools(self):
        """
        Bloque 13: registra las herramientas de ejecución real sobre el
        mundo (run_command / open_app / media_control).

        Son permission="ask": la compuerta SecurityGate las decide con
        grants (manual → autorización humana; assisted/autonomous → grant
        activo). Sus handlers delegan en la integración YA decidida
        (pre_authorized=True) para que un grant de un solo uso no se
        consuma dos veces (una en la compuerta y otra en la integración).
        create_tool_manager() (5 tools) y su test quedan intactos.
        """
        integrations = self.integrations

        self.tools.register(
            name="run_command",
            description=(
                "Ejecutar un comando de la lista blanca del sistema "
                "(shell=False, timeout y salida acotada)."
            ),
            handler=lambda integrations=integrations, **kwargs: (
                integrations.execute(
                    "run_command", pre_authorized=True, **kwargs
                )
            ),
            permission="ask",
            schema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": (
                            "Línea de comando a ejecutar (lista blanca)."
                        )
                    },
                    "timeout": {
                        "type": "integer",
                        "description": (
                            "Máximo de segundos de ejecución."
                        )
                    }
                },
                "required": ["command"]
            }
        )

        self.tools.register(
            name="open_app",
            description=(
                "Abrir una aplicación de la lista blanca del sistema."
            ),
            handler=lambda integrations=integrations, **kwargs: (
                integrations.execute(
                    "open_app", pre_authorized=True, **kwargs
                )
            ),
            permission="ask",
            schema={
                "type": "object",
                "properties": {
                    "app": {
                        "type": "string",
                        "description": (
                            "Nombre de la app a abrir (lista blanca)."
                        )
                    }
                },
                "required": ["app"]
            }
        )

        self.tools.register(
            name="media_control",
            description=(
                "Controlar la reproducción multimedia del sistema "
                "(reproducir, pausar, siguiente, anterior, volumen, mute)."
            ),
            handler=lambda integrations=integrations, **kwargs: (
                integrations.execute(
                    "media_control", pre_authorized=True, **kwargs
                )
            ),
            permission="ask",
            schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": (
                            "Acción canónica de media: play, pause, "
                            "play-pause, next, previous, volume-up, "
                            "volume-down, mute, unmute."
                        ),
                        "enum": [
                            "play", "pause", "play-pause",
                            "next", "previous",
                            "volume-up", "volume-down",
                            "mute", "unmute"
                        ]
                    }
                },
                "required": ["action"]
            }
        )

    def _extract_run_command(self, message):
        """Extrae el comando de 'ejecutá/ejecuta/corré/corre <comando>'."""
        lowered = message.lower().strip()

        markers = ("ejecutá ", "ejecuta ", "corré ", "corre ")

        for marker in markers:
            if not lowered.startswith(marker):
                continue

            command = message[len(marker):].strip(":.,; ")

            # Tolerar 'ejecutá el comando ls', 'ejecutá un comando ls'.
            for prefix in (
                "el comando ", "un comando ", "este comando ",
                "el comando:", "un comando:",
            ):
                if command.lower().startswith(prefix):
                    command = command[len(prefix):].strip()
                    break

            return command

        return ""

    def _extract_open_app(self, message):
        """
        Extrae la app de 'abrí/abre <app>'.

        NUNCA alcanza 'abre el archivo' (se resuelve como read_file ANTES,
        en _identify_tool). Solo despacha si la app está en la lista blanca
        de CommandPolicy: si no, devuelve "" y la frase cae al modelo.
        """
        lowered = message.lower().strip()

        markers = ("abrí ", "abre ")

        for marker in markers:
            if not lowered.startswith(marker):
                continue

            app = message[len(marker):].strip(":.,; ")

            # Tolerar 'abrí la aplicación X' / 'abrí el programa X'.
            for prefix in (
                "la aplicacion ", "la aplicación ", "la app ",
                "el programa ",
            ):
                if app.lower().startswith(prefix):
                    app = app[len(prefix):].strip()
                    break

            if not app:
                return ""

            if self.integrations.command_policy.app_allowed(app):
                return app

            return ""

        return ""

    def _extract_media_action(self, message):
        """Extrae la acción canónica de media de frases de control."""
        lowered = message.lower().strip()

        mapping = (
            # Las más específicas primero, para no comerse a las generales.
            ("pausá la música", "pause"),
            ("pausa la música", "pause"),
            ("pausá la canción", "pause"),
            ("pausa la canción", "pause"),
            ("pausá", "pause"),
            ("pausa", "pause"),
            ("reproducí la música", "play"),
            ("reproduce la música", "play"),
            ("reproducí la canción", "play"),
            ("reproduce la canción", "play"),
            ("seguí reproduciendo", "play"),
            ("seguí con la reproducción", "play"),
            ("reproducí", "play"),
            ("reproduce", "play"),
            ("siguiente canción", "next"),
            ("canción siguiente", "next"),
            ("pasa a la siguiente", "next"),
            ("adelantá la canción", "next"),
            ("adelanta la canción", "next"),
            ("adelantá", "next"),
            ("adelanta", "next"),
            ("canción anterior", "previous"),
            ("anterior canción", "previous"),
            ("volvé a la anterior", "previous"),
            ("subí el volumen un poco", "volume-up"),
            ("subí el volumen", "volume-up"),
            ("subí el volúmen", "volume-up"),
            ("sube el volumen", "volume-up"),
            ("bajá el volumen un poco", "volume-down"),
            ("bajá el volumen", "volume-down"),
            ("bajá el volúmen", "volume-down"),
            ("baja el volumen", "volume-down"),
            ("silenciá", "mute"),
            ("silencia", "mute"),
            ("ponelo en mute", "mute"),
            ("activá el sonido", "unmute"),
            ("activa el sonido", "unmute"),
        )

        for phrase, action in mapping:
            if phrase in lowered:
                return action

        return ""

    def _tool_arguments(self, tool_name, message):
        """Argumentos deterministas para una herramienta directa."""
        if tool_name == "web_search":
            return {"query": self._extract_web_query(message)}

        if tool_name == "read_file":
            return {"path": self._extract_file_path(message)}

        if tool_name == "notify":
            return {"message": self._extract_notify_message(message)}

        if tool_name == "run_command":
            return {"command": self._extract_run_command(message)}

        if tool_name == "open_app":
            return {"app": self._extract_open_app(message)}

        if tool_name == "media_control":
            return {"action": self._extract_media_action(message)}

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
            "run_command": "command",
            "open_app": "app",
            "media_control": "action",
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
            "autoriza siempre ",
            "autorizá siempre ",
            "concede permiso para ",
            "concedé permiso para ",
            "concede permiso siempre para ",
            "concedé permiso siempre para ",
            "da permiso para ",
            "dá permiso para ",
            "da permiso siempre para ",
            "dá permiso siempre para ",
            "recuerda que puedes ",
            "recuerda que podés ",
            "recuerda el permiso para ",
            "recuerda el permiso de ",
            "recuerda permiso para ",
        )

        for prefix in grant_prefixes:
            if lowered.startswith(prefix):
                target = message[len(prefix):].strip().strip(".")
                target = target.strip()

                if not target:
                    return None

                # ---- Bloque 14: clave de autorización (PIN) ----
                # Conceder un permiso por voz/texto exige que la persona
                # demuestre la clave del owner. Se valida acá en código
                # determinista; el modelo JAMÁS ve la clave. Fail-closed:
                # sin clave configurada o sin clave en el mensaje, no se
                # concede. El PIN se identifica como un número de 6 cifras
                # y se REMUEVE del target antes de parsear la capacidad,
                # de modo que soporta "autoriza run_command 240890" y
                # "autoriza con clave 240890 run_command".
                pin_matches = re.findall(r"\b\d{6}\b", message)
                pin = pin_matches[0] if pin_matches else None

                if pin is not None:
                    target = re.sub(r"\b\d{6}\b", "", target).strip()

                capability = target.split()[-1].strip(".,;:").lower()

                # "autoriza X" concede para UNA acción (menor privilegio).
                # "autoriza siempre X" / "recuerda que puedes X" emiten un
                # permiso RECORDADO (indefinido pero revocable), de modo
                # que I.L.U. pueda volver a usarlo sin re-preguntar.
                remembered = (
                    "siempre" in lowered
                    or "recuerda" in lowered
                )

                # Solo capacidades de ejecución; jamás autoridad,
                # modificación de política ni autoconcesión. Este check
                # va PRIMERO y sin clave: a quien intenta "autoriza shell"
                # se le rechaza igual que antes, sin pedirle nada.
                if self.policy.is_prohibited(capability):
                    return self._authority_reply(
                        message,
                        "No puedo otorgar un permiso para una acción "
                        "prohibida por policy.",
                        "permission_error",
                        error="capability_prohibited",
                    )

                if not self.owner_secret.configured:
                    return self._authority_reply(
                        message,
                        "La clave de autorización no está configurada.",
                        "permission_error",
                        error="owner_pin_unconfigured",
                    )

                if pin is None:
                    return self._authority_reply(
                        message,
                        "Para autorizar, decime tu clave.",
                        "permission_error",
                        error="owner_pin_required",
                    )

                if not self.owner_secret.matches(pin):
                    # Se audita el intento fallido: la compuerta nunca
                    # concede y deja rastro.
                    self.audit.record(
                        actor=self.settings.owner_id,
                        action="owner_secret_failed",
                        reason="wrong_pin",
                        decision="deny",
                        capability=capability,
                    )
                    return self._authority_reply(
                        message,
                        "Clave incorrecta. No otorgué el permiso.",
                        "permission_error",
                        error="owner_pin_denied",
                    )

                try:
                    grant = self.authority.grant(
                        capability=capability,
                        actor=self.settings.owner_id,
                        reason=(
                            "recordado por owner por lenguaje natural"
                            if remembered
                            else "solicitado por owner por lenguaje natural"
                        ),
                        origin=(
                            "nl_owner_command_remembered"
                            if remembered
                            else "nl_owner_command"
                        ),
                        indefinite=remembered,
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
                    (
                        f"Permiso recordado: '{capability}' (ID {grant.key}). "
                        "Puedo usarlo sin volver a preguntar; revócalo "
                        "cuando quieras."
                        if remembered
                        else f"Permiso otorgado: '{capability}' "
                        f"(ID {grant.key})."
                    ),
                    "permission_granted",
                    grant={
                        "grant_id": grant.key,
                        "capability": grant.capability,
                        "level": grant.level,
                        "scope_type": grant.scope_type,
                        "indefinite": grant.indefinite,
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

    # ------------------------------------------------------------------
    # JARVIS Evolution: planificación, aprendizaje, identidad,
    # proactividad, percepción e integraciones por lenguaje natural.
    #
    # Ninguno de estos comandos otorga permisos: solo organiza intenciones
    # (planes), escribe en la memoria propia de I.L.U. (aprendizaje),
    # reconoce identidades para personalizar, o lee el entorno. Las
    # integraciones sobre el mundo siempre exigen grant activo.
    # ------------------------------------------------------------------

    def _javis_command(self, message):
        lowered = message.lower().strip()

        # ----------------------------------------------------------
        # PLANIFICACIÓN / OBJETIVOS
        # ----------------------------------------------------------
        plan_prefixes = (
            "planifica ",
            "planificá ",
            "crea un plan para ",
            "creá un plan para ",
            "haz un plan para ",
            "hacé un plan para ",
            "establece un objetivo: ",
            "establece el objetivo ",
            "armá un plan para ",
            "arma un plan para ",
        )

        for prefix in plan_prefixes:
            if lowered.startswith(prefix):
                objective = message[len(prefix):].strip().strip(":.,;")

                if not objective:
                    return {
                        "success": True,
                        "input": message,
                        "intent": "plan_ask",
                        "response": (
                            "¿Qué objetivo quieres que planifique? "
                            "Ejemplo: planifica organizar la mudanza."
                        ),
                        "core": self.name,
                        "version": self.version,
                    }

                # Pasos explícitos separados por coma se respetan; si no,
                # el plan usa fases por defecto.
                if "," in objective:
                    steps = [
                        part.strip() for part in objective.split(",")
                        if part.strip()
                    ]
                    goal = self.planner.create(objective, steps=steps)
                else:
                    goal = self.planner.create(objective)

                self.audit.record(
                    actor="ilu",
                    action="goal_create",
                    goal_id=goal["id"],
                    objective=objective,
                )

                steps_text = " | ".join(
                    f"{i + 1}. {step['title']}"
                    for i, step in enumerate(goal["steps"])
                )

                return {
                    "success": True,
                    "input": message,
                    "intent": "plan_create",
                    "response": (
                        f"Objetivo creado: {goal['title']}. "
                        f"Plan: {steps_text}."
                    ),
                    "goal": goal,
                    "core": self.name,
                    "version": self.version,
                }

        plan_list_phrases = (
            "mis planes",
            "mis objetivos",
            "lista de planes",
            "listar planes",
            "qué objetivos tengo",
            "que objetivos tengo",
            "estado de mis objetivos",
            "estado de mis planes",
        )

        if any(phrase in lowered for phrase in plan_list_phrases):
            goals = self.planner.list()

            if not goals:
                return self._javis_reply(
                    message,
                    "No tengo objetivos planificados todavía.",
                    "plan_list",
                    goals=[],
                )

            lines = [
                f"{goal['title']} — {goal['status']} "
                f"({self.planner.progress(goal['id'])['percent']}%)"
                for goal in goals[:10]
            ]

            return self._javis_reply(
                message,
                "Mis objetivos: " + " | ".join(lines),
                "plan_list",
                goals=[
                    {
                        "id": goal["id"],
                        "title": goal["title"],
                        "status": goal["status"],
                        "progress": self.planner.progress(goal["id"]),
                    }
                    for goal in goals[:10]
                ],
            )

        # ----------------------------------------------------------
        # APRENDIZAJE / PERSONALIZACIÓN
        # ----------------------------------------------------------
        learn_phrases = (
            "qué has aprendido de mí",
            "que has aprendido de mi",
            "qué aprendiste sobre mí",
            "que aprendiste sobre mi",
            "qué sabes de mí",
            "que sabes de mi",
            "muéstrame tu perfil",
            "muestrame tu perfil",
            "qué has aprendido sobre mi",
            "que has aprendido sobre mi",
        )

        if any(phrase in lowered for phrase in learn_phrases):
            summary = self.learning.summary()
            profile = self.learning.profile()

            return self._javis_reply(
                message,
                summary,
                "learning_profile",
                profile=profile,
            )

        # ----------------------------------------------------------
        # IDENTIDAD / RECONOCIMIENTO
        # ----------------------------------------------------------
        identity_phrases = (
            "quién soy",
            "quien soy",
            "reconóceme",
            "reconceme",
            "¿quién crees que soy",
            "quien crees que soy",
            "a quién estás hablando",
        )

        if any(phrase in lowered for phrase in identity_phrases):
            recognition = self.recognizer.recognize(message)

            if not recognition["recognized"]:
                response = (
                    "Todavía no puedo identificarte con certeza. "
                    "Dime tu nombre o configúrame tu identidad y aliases."
                )
            else:
                kind_label = {
                    "owner": "mi owner",
                    "family_member": "un miembro de la familia",
                    "authorized_user": "un usuario autorizado",
                    "ilu": "I.L.U. misma",
                }.get(recognition["kind"], recognition["kind"])

                response = (
                    f"Te reconozco como {kind_label} "
                    f"({recognition['principal_id']}), por "
                    f"{recognition['method']}."
                )

            return self._javis_reply(
                message,
                response,
                "identity_recognition",
                recognition=recognition,
            )

        # ----------------------------------------------------------
        # PROACTIVIDAD / RECORDATORIOS
        # ----------------------------------------------------------
        reminder_prefixes = (
            "recuérdame ",
            "recordame ",
            "recuerdame ",
            "avisame que ",
            "avísame que ",
        )

        for prefix in reminder_prefixes:
            if lowered.startswith(prefix):
                rest = message[len(prefix):].strip()

                # Formato: "... en N minutos" / "... en N horas"
                import re as _re
                match = _re.search(
                    r"\ben\s+(\d+)\s*(minuto|minutos|min|hora|horas|h)\b",
                    rest,
                )

                if not match:
                    return {
                        "success": True,
                        "input": message,
                        "intent": "reminder_ask",
                        "response": (
                            "¿En cuánto tiempo? Dime: "
                            "'recuérdame X en 30 minutos'."
                        ),
                        "core": self.name,
                        "version": self.version,
                    }

                amount = int(match.group(1))
                unit = match.group(2)

                if unit in ("hora", "horas", "h"):
                    minutes = amount * 60
                else:
                    minutes = amount

                text = rest[:match.start()].strip(" .,;:")

                if not text:
                    return None

                rule = self.proactivity.add(
                    kind="reminder",
                    text=text,
                    cadence_minutes=minutes,
                )

                self.audit.record(
                    actor="ilu",
                    action="reminder_create",
                    rule_id=rule["id"],
                    text=text,
                )

                return self._javis_reply(
                    message,
                    f"Te lo recuerdo en {amount} {unit}: '{text}'.",
                    "reminder_create",
                    rule={
                        "id": rule["id"],
                        "kind": rule["kind"],
                        "text": rule["text"],
                    },
                )

        reminder_list_phrases = (
            "mis recordatorios",
            "lista de recordatorios",
            "qué recordatorios tengo",
            "que recordatorios tengo",
        )

        if any(phrase in lowered for phrase in reminder_list_phrases):
            rules = self.proactivity.list()

            if not rules:
                return self._javis_reply(
                    message,
                    "No tengo recordatorios activos.",
                    "reminder_list",
                    reminders=[],
                )

            lines = [
                f"{rule['text']} ({rule['kind']})"
                for rule in rules[:10]
            ]

            return self._javis_reply(
                message,
                "Mis recordatorios: " + " | ".join(lines),
                "reminder_list",
                reminders=[
                    {
                        "id": rule["id"],
                        "kind": rule["kind"],
                        "text": rule["text"],
                        "enabled": rule["enabled"],
                    }
                    for rule in rules[:10]
                ],
            )

        # ----------------------------------------------------------
        # PERCEPCIÓN / SENSORES
        # ----------------------------------------------------------
        perceive_phrases = (
            "qué ves",
            "que ves",
            "qué sensores tienes",
            "que sensores tienes",
            "percibí el entorno",
            "percibe el entorno",
            "qué percibes",
            "que percibes",
        )

        if any(phrase in lowered for phrase in perceive_phrases):
            caps = self.perception.list_capabilities()
            all_data = self.perception.perceive_all()

            available = [
                c["capability"] for c in caps if c["available"]
            ]

            response = (
                "Mis sensores disponibles: "
                + (", ".join(available) if available else "ninguno")
                + "."
            )

            return self._javis_reply(
                message,
                response,
                "perception_status",
                capabilities=caps,
                perception=all_data,
            )

        return None

    def _javis_reply(self, message, response, intent, **extra):
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

    def _verification_ok(self, actor):
        """
        D-7 — ¿La identidad que actúa está verificada?

        Una identidad se considera verificada si es I.L.U. misma ("ilu")
        o un principal raíz registrado (el owner). Cualquier otra identidad
        que intente una operación sensible queda como NO verificada y,
        ante fallos repetidos, la marca SpoofingGuard como sospechosa.
        """
        if actor == "ilu":
            return True

        try:
            return self.principals.is_root(actor)
        except Exception:
            return False

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

        # Bloque 11: se valida el esquema ANTES de pasar por la compuerta y
        # de ejecutar. Unos argumentos inválidos se rechazan de forma
        # honesta (fail-closed); una tool sin esquema siempre pasa
        # (retrocompatibilidad).
        schema_ok, schema_error = toolshape.validate_arguments(
            self.tools.get_schema(tool_call.tool),
            tool_call.arguments
        )

        if not schema_ok:
            self.audit.record(
                actor="ilu",
                action="tool_attempt",
                tool=tool_call.tool,
                decision="deny",
                mode=mode,
                reason=schema_error
            )

            return {
                "success": False,
                "error": schema_error,
                "tool": tool_call.tool,
                "validation": "failed"
            }

        permission = self.tools.get_permission(
            tool_call.tool
        )

        # D-7: se conecta el SpoofingGuard y la verificación de identidad.
        # I.L.U. actúa como "ilu" (verificada); una identidad desconocida
        # en una capacidad sensible queda bajo vigilancia de suplantación.
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
            spoofing=self.spoofing,
            verification_ok=self._verification_ok("ilu"),
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

        # Verificación: tras ejecutar, I.L.U. comprueba el resultado y lo
        # deja rastreado. Esto cierra el bucle ejecutar→verificar→aprender.
        self._verify_tool_result(tool_call, result)

        return result

    def _verify_tool_result(self, tool_call, result):
        """
        Verifica el resultado de una herramienta tras ejecutarla.

        - Registra si el resultado fue exitoso o no (fail/honestidad).
        - Si una tarea materializada de un objetivo se completó, avanza
          el paso del plan (verificación → planificación conectadas).

        No otorga permisos: solo lee el resultado y actualiza el estado
        del plan de I.L.U.
        """
        try:
            succeeded = bool(result.get("success"))
        except Exception:
            succeeded = False

        self.audit.record(
            actor="ilu",
            action="tool_verify",
            tool=tool_call.tool,
            success=succeeded,
            mode="verification",
        )

        return succeeded

    def _build_tool_response(
        self,
        message,
        tool_call,
        tool_result,
        source="direct_tool",
        session_id="default"
    ):
        if not tool_result:
            return None

        # La conciencia unificada viaja con TODA respuesta (éxito y
        # error por igual) para que la UI pueda renderizar la presencia
        # de I.L.U. aunque el turno haya terminado en una herramienta.
        awareness = self._build_awareness(
            message,
            session_id
        )
        awareness_ctx = self._awareness_context(awareness)

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
                # Bloque 13: rechazos honestos y legibles del mundo.
                world_errors = {
                    "command_not_allowlisted": (
                        "Ese comando no está en la lista blanca de I.L.U."
                    ),
                    "command_token_rejected": (
                        "Ese comando usa operadores de shell vetados "
                        "(pipes, redirección o sustitución)."
                    ),
                    "command_required": (
                        "No me indicaste qué comando ejecutar."
                    ),
                    "command_policy_unavailable": (
                        "La política de comandos no está disponible; no "
                        "ejecuto nada hasta que se restablezca."
                    ),
                    "app_not_allowed": (
                        "Esa aplicación no está en la lista blanca."
                    ),
                    "app_not_found": (
                        "No encontré esa aplicación instalada en el sistema."
                    ),
                    "app_launch_failed": "No pude abrir la aplicación.",
                    "media_action_invalid": (
                        "Esa acción de multimedia no es válida para I.L.U."
                    ),
                    "media_backend_unavailable": (
                        "El backend de audio (playerctl) no está "
                        "disponible en este sistema."
                    ),
                }
                response = world_errors.get(
                    tool_result.get("error"),
                    "I.L.U. no pudo ejecutar la herramienta.",
                )

            return {
                "success": False,
                "input": message,
                "intent": "tool_error",
                "response": response,
                "context": "",
                "awareness": awareness,
                "awareness_context": awareness_ctx,
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
        tool_name = tool_call.tool if tool_call else None

        if (
            isinstance(result, dict)
            and "datetime" in result
        ):
            response = (
                "La fecha y hora de tu sistema es: "
                f"{result['datetime']}"
            )
        elif tool_name == "run_command":
            # Bloque 13: formato humano para ejecución real.
            exit_code = result.get("exit_code", 0)
            stdout_text = (result.get("stdout") or "").strip()
            stderr_text = (result.get("stderr") or "").strip()

            if exit_code != 0:
                response = (
                    "El comando terminó con error "
                    f"(código {exit_code}): "
                    f"{stderr_text or stdout_text}"
                )
            elif stdout_text:
                response = f"Ejecuté: {stdout_text}"
            else:
                response = "Comando ejecutado correctamente."

            if result.get("truncated"):
                response += " (salida recortada)"
        elif tool_name == "open_app":
            response = f"Abrí {result.get('app') or 'la aplicación'}."

            if result.get("pid"):
                response += f" (PID {result['pid']})"
        elif tool_name == "media_control":
            labels = {
                "play": "Reproducción activada.",
                "pause": "Reproducción en pausa.",
                "play-pause": "Reproducción alternada.",
                "next": "Pasó a la siguiente canción.",
                "previous": "Volvió a la canción anterior.",
                "volume-up": "Subí el volumen.",
                "volume-down": "Bajé el volumen.",
                "mute": "Sonido silenciado.",
                "unmute": "Sonido activado.",
            }
            response = labels.get(
                result.get("action"),
                "Acción de multimedia aplicada.",
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
            "awareness": awareness,
            "awareness_context": awareness_ctx,
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
        # 0.7 JARVIS EVOLUTION: planificación, aprendizaje, identidad,
        #     proactividad y percepción por lenguaje natural.
        #
        # Son comandos organizativos/lectura sobre el dominio propio de
        # I.L.U. (planes, memoria, identidad, recordatorios, sensores);
        # ninguno concede permisos. Se evalúan aquí para resolver sin
        # depender del LLM cuando el mensaje es un comando directo.
        # ==========================================================

        javis_command = self._javis_command(message)

        if javis_command is not None:
            return javis_command

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
                direct_tool_result,
                session_id=session_id
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

        # Conciencia unificada: I.L.U. entra a razonar sabiendo quién es,
        # con quién habla, qué ha aprendido, qué objetivos tiene, qué
        # percibe y qué tiene pendiente. Se inyecta ANTES del historial y
        # de la memoria bruta, como estado actual (no como memoria).
        awareness = self._build_awareness(message, session_id)
        model_context = self._awareness_context(awareness) + model_context

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
                    source="model_tool",
                    session_id=session_id
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

        # JARVIS Evolution (Bloque B): aprendizaje pasivo. I.L.U. distila
        # de la conversación la información estable y de alto valor
        # (preferencias, datos personales, proyectos, hechos) hacia su
        # memoria semántica para personalizarse con el tiempo. Solo escribe
        # en su propia memoria; nunca ejecuta ni concede permisos.
        try:
            self.learning.learn(message)
        except Exception:
            # El aprendizaje es best-effort: un fallo aquí no debe romper
            # la respuesta de la conversación.
            pass

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

        # La conciencia unificada viaja con la respuesta para que la UI
        # pueda renderizar la presencia de I.L.U. (estado, objetivos,
        # percepción, pendientes) como una única entidad.
        awareness_ctx = self._awareness_context(awareness)

        return {
            "success": True,
            "input": message,
            "intent": intent,
            "response": response,
            "context": self._format_memories(
                context
            ),
            "awareness": awareness,
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
            "version": self.version,
            "awareness_context": awareness_ctx,
        }
