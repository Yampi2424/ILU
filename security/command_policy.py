"""
I.L.U. — Política de comandos del mundo (Bloque 13: ejecución real gateada).

Un grant para `run_command` autoriza la CAPACIDAD de ejecutar comandos; esta
política decide QUÉ comandos exactos se pueden ejecutar. Son dos diales
independientes y ambos fail-closed:

  - App / Accreditation: cuál identidad puede (grants en GrantStore).
  - Aquí: qué está permitido ejecutar, abrir o controlar.

Estructura de `security/run_commands.json` (commiteado, como policy.json):

    {
      "version": 1,
      "allowlist": ["ls", "pwd", "whoami", ...],   // comandos permitidos (1er token)
      "apps": ["firefox", "brave", ...],           // aplicaciones que se pueden abrir
      "media": ["playerctl"],                      // backends de control multimedia
      "deny_substrings": [";", "&&", "|", ...],    // metachars vetados en CUALQUIER token
      "default_timeout": 15,                       // segundos por comando
      "max_output_bytes": 8192                     // tamaño máximo de salida capturada
    }

Regla de oro: `shell` crudo sigue PROHIBIDO en policy.json. Este módulo solo
vetas la vía SANCIONADA de ejecución: `shell=False`, sin pipes/redirección
(los metachars se rechazan token a token) y limitada a la lista blanca.

Si el archivo no existe o está corrupto, la lista queda VACÍA (fail-closed):
no se ejecuta ni se abre nada, con un error explícito.
"""

import json
import os
import shlex


# --- Acciones de control multimedia (mapeo ES/canónico -> playerctl) --------
#
# La acción canónica (que el modelo propone y el despacho NL resuelve) se
# traduce a los argumentos exactos del backend. Se mantienen acotadas: I.L.U.
# jamás construye libremente un comando de media, solo estas acciones.
PLAYERCTL_ACTIONS = {
    "play": ["play"],
    "pause": ["pause"],
    "play-pause": ["play-pause"],
    "next": ["next"],
    "previous": ["previous"],
    "volume-up": ["volume", "+0.05"],
    "volume-down": ["volume", "-0.05"],
    "mute": ["volume", "0"],
    "unmute": ["volume", "1"],
}

MEDIA_ACTIONS = tuple(PLAYERCTL_ACTIONS)

# Set por defecto: SOLO comandos de lectura/inspección. Cualquier comando
# potencialmente destructivo (rm, sudo, shutdown, dd, mkfs...) queda FUERA:
# requiere que el owner lo agregue deliberadamente a run_commands.json.
_DEFAULT_ALLOWLIST = ("ls", "pwd", "whoami", "date", "uname", "hostname")

_DEFAULT_APPS = ("firefox", "brave", "code", "vlc")
_DEFAULT_MEDIA = ("playerctl",)

# Metachars/sustratos vetados en cualquier token. Rechazan pipes, redirección,
# sustitución de comandos y traversal: la ejecución es SIEMPRE shell=False.
_DEFAULT_DENY_SUBSTRINGS = (";", "&&", "||", "|", ">", "<", "$", "`", "..")

_DEFAULT_TIMEOUT = 15
_DEFAULT_MAX_OUTPUT = 8192


class CommandPolicy:
    """
    Fuente de verdad de qué acciones sobre el mundo están permitidas.

    Carga de DISCO (commiteado). Cualquier archivo ausente/corrupto deja la
    lista vacía: la decisión de no ejecutar es explícita y silenciosa en el
    peor caso, nunca un "por defecto abierto".
    """

    def __init__(self, path=None):
        if path is None:
            path = os.environ.get(
                "ILU_RUN_COMMANDS_PATH",
                "security/run_commands.json"
            )
        self.path = path

        # Estado del archivo: si no se cargó bien, todo queda cerrado.
        self._ok = False
        self.allowlist = []
        self.apps = []
        self.media = []
        self.deny_substrings = list(_DEFAULT_DENY_SUBSTRINGS)
        # Confinamientos; el guion bajo evita pisar los métodos del mismo
        # nombre (default_timeout()/max_output_bytes()).
        self._default_timeout = _DEFAULT_TIMEOUT
        self._max_output_bytes = _DEFAULT_MAX_OUTPUT

        self._load()

    # ------------------------------------------------------------------
    # Carga
    # ------------------------------------------------------------------

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            # Ausente o corrupto -> fail-closed (listas vacías).
            return

        if not isinstance(data, dict):
            return

        allowlist = data.get("allowlist", _DEFAULT_ALLOWLIST)
        apps = data.get("apps", _DEFAULT_APPS)
        media = data.get("media", _DEFAULT_MEDIA)
        deny = data.get("deny_substrings", _DEFAULT_DENY_SUBSTRINGS)

        if not isinstance(allowlist, list):
            allowlist = []
        if not isinstance(apps, list):
            apps = []
        if not isinstance(media, list):
            media = []
        if not isinstance(deny, list):
            deny = []

        self.allowlist = [
            str(item).strip() for item in allowlist
            if str(item).strip()
        ]
        self.apps = [
            str(item).strip() for item in apps
            if str(item).strip()
        ]
        self.media = [
            str(item).strip() for item in media
            if str(item).strip()
        ]
        self.deny_substrings = [
            str(item) for item in deny
            if str(item)
        ]

        timeout = data.get("default_timeout", _DEFAULT_TIMEOUT)
        output = data.get("max_output_bytes", _DEFAULT_MAX_OUTPUT)

        # Override de confinamientos por entorno (runtime tuning) sobre los
        # valores del archivo; es UNA sola fuente efectiva: primero el env.
        timeout = os.environ.get("ILU_WORLD_TIMEOUT", timeout)
        output = os.environ.get("ILU_WORLD_MAX_OUTPUT", output)

        try:
            self._default_timeout = int(timeout)
        except (TypeError, ValueError):
            self._default_timeout = _DEFAULT_TIMEOUT

        try:
            self._max_output_bytes = int(output)
        except (TypeError, ValueError):
            self._max_output_bytes = _DEFAULT_MAX_OUTPUT

        self._ok = True

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------

    def available(self):
        """¿El archivo de política se cargó correctamente? La lista blanca
        vacía por fallo de carga NO equivale a "todo permitido"."""
        return self._ok

    def default_timeout(self):
        return max(1, self._default_timeout or _DEFAULT_TIMEOUT)

    def max_output_bytes(self):
        return max(1, self._max_output_bytes or _DEFAULT_MAX_OUTPUT)

    def app_allowed(self, app):
        return app in self.apps

    def media_backend(self):
        """Backend configurado de control multimedia (p. ej. 'playerctl')."""
        return self.media[0] if self.media else None

    def media_args(self, action):
        """
        Traduce una acción canónica de media a los argumentos del backend.
        Devuelve (args, backend) si la acción es válida y hay backend;
        si no, (None, None).
        """
        backend = self.media_backend()

        if backend is None or action not in PLAYERCTL_ACTIONS:
            return None, None

        return list(PLAYERCTL_ACTIONS[action]), backend

    def validate_command(self, cmdline):
        """
        Valida una línea de comando contra la lista blanca.

        Devuelve (ok, value):
          - (True, [tokens])  si comando permitido.
          - (False, "error")  si no (fail-closed, con motivo legible).

        La línea se parte con shlex (respeta comillas); el PRIMER token debe
        estar en la allowlist y NINGÚN token puede contener un sustrato vetado
        (metachars de shell, traversal). shell=False en la ejecución es el
        último candado, no este check.
        """
        if not self._ok:
            return False, "command_policy_unavailable"

        if not isinstance(cmdline, str) or not cmdline.strip():
            return False, "command_required"

        try:
            tokens = shlex.split(cmdline)
        except ValueError:
            return False, "command_malformed"

        if not tokens:
            return False, "command_required"

        command = tokens[0]

        if command not in self.allowlist:
            return False, "command_not_allowlisted"

        for token in tokens:
            for bad in self.deny_substrings:
                if bad in token:
                    return False, "command_token_rejected"

        return True, tokens