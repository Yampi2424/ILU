"""
Motor de Skills de I.L.U. (Fase B — catálogo y ejecución gateada).

Alcanza la capacidad de *skills* de OpenJarvis (agentskills.io como
inspiración de formato) SIN depender de él y SIN ceder la compuerta:

Reglas de oro de seguridad:
- Una skill NUNCA se concede permisos a sí misma. No posee access a
  Authority ni a GrantStore: solo puede ejecutar herramientas a través
  del callable `run_tool` que el núcleo le inyecta (el MISMO
  `core._execute_tool_call`, en modo "skill"), que pasa por SecurityGate,
  audita y abre AuthorizationRequest si hace falta.
- Una herramienta no registrada o denegada por la compuerta NO se
  ejecuta (fail-closed) y el skill se detiene reportando el fallo.
- Los pasos `synthesize` solo llaman al proveedor con el material
  recolectado (generación de texto; el proveedor nunca ejecuta tools).
- Sub-skills con límite de profundidad y guard anti-ciclos (fail-closed).
"""

import os
import re
from dataclasses import dataclass, field

from tools.call import ToolCall


# ----------------------------------------------------------------------
# Formato del catálogo
# ----------------------------------------------------------------------
#
# Cada skill es un archivo Markdown con frontmatter `---` tolerante:
#
#   ---
#   name: slug-único
#   description: qué hace (usada también para síntesis)
#   variables:            # opcional
#     - nombre
#   steps:
#     - tool: herramienta_registrada
#       args:
#         clave: "valor {var}"
#       note: "frase natural para la UI"
#     - skill: otra-skill
#       note: "…"
#     - synthesize: true
#       note: "Sintetizo el material…"
#   ---
#
# Cuerpo: texto libre de documentación (no se ejecuta).
# ----------------------------------------------------------------------


@dataclass
class SkillStep:
    """Un paso de una skill: tool gateada, sub-skill o síntesis."""
    kind: str                 # "tool" | "skill" | "synthesize"
    note: str = ""
    tool: str = ""
    args: dict = field(default_factory=dict)
    skill: str = ""
    synthesize: bool = False


@dataclass
class Skill:
    """Skill parseada del catálogo."""
    name: str
    description: str
    steps: list = field(default_factory=list)
    variables: list = field(default_factory=list)
    raw: str = ""

    def to_dict(self):
        return {
            "name": self.name,
            "description": self.description,
            "variables": list(self.variables),
            "step_count": len(self.steps),
            "steps": [
                {
                    "kind": step.kind,
                    "note": step.note,
                    "tool": step.tool or step.skill or "",
                }
                for step in self.steps
            ],
        }


# ----------------------------------------------------------------------
# Parser del frontmatter (stdlib puro, tolerante)
# ----------------------------------------------------------------------

_SIMPLE_KEY = re.compile(r"^([a-zA-Z0-9_-]+)\s*:\s*(.*)$")
_LIST_ITEM = re.compile(r"^\s*-\s+(.*)$")


def _parse_header(lines):
    """Convierte las líneas del frontmatter en un dict simple."""
    data = {}
    list_key = None           # clave lista en la que estamos ("steps")
    sub = None                # dict del bloque "- key: value" dentro de steps
    indent_guard = None

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # --- bloque de lista al nivel actual ---
        m_list = _LIST_ITEM.match(line)
        if m_list:
            item_text = m_list.group(1).strip()
            # ¿es un item de "steps:"? Se detecta si venimos de "steps:"
            if list_key == "steps":
                # Item de paso: "- tool: X" / "- skill: Y" / "- synthesize: true"
                pm = _SIMPLE_KEY.match(item_text)
                if pm:
                    sub = {}
                    sub[pm.group(1)] = _coerce(pm.group(2))
                    data.setdefault("steps", []).append(sub)
                i += 1
                continue

            if list_key:
                data.setdefault(list_key, []).append(_coerce(item_text))
                i += 1
                continue

            i += 1
            continue

        # --- continuaciones de un paso (sub-keys indentadas: args/note) ---
        # Debe ir ANTES del match general de clave:valor para capturar
        # claves indentadas dentro de un step (args:, note:)
        if list_key == "steps" and sub is not None and line.startswith("  "):
            sm = _SIMPLE_KEY.match(stripped)
            if sm:
                skey, svalue = sm.group(1), sm.group(2).strip()
                if skey == "args":
                    sub["args"] = {}
                    # args hasta próxima key sin doble indent (6+ espacios)
                    i += 1
                    while i < len(lines):
                        a_line = lines[i]
                        if not a_line.startswith("      "):
                            break
                        am = _SIMPLE_KEY.match(a_line.strip())
                        if am:
                            sub["args"][am.group(1)] = _coerce(
                                am.group(2).strip()
                            )
                        i += 1
                    continue
                sub[skey] = _coerce(svalue)
            i += 1
            continue

        # --- clave: valor al nivel actual ---
        m_key = _SIMPLE_KEY.match(line)
        if m_key:
            key, value = m_key.group(1), m_key.group(2).strip()
            list_key = None

            if value in (">", "|", ">-", "|-"):
                # bloque multilínea hasta la próxima clave/índice
                block = []
                i += 1
                while i < len(lines):
                    nxt = lines[i]
                    if (
                        nxt.strip()
                        and (_SIMPLE_KEY.match(nxt) or _LIST_ITEM.match(nxt))
                        and not nxt.startswith("  ")
                    ):
                        break
                    if nxt.strip():
                        block.append(_coerce(nxt.strip()))
                    i += 1
                data[key] = "\n".join(block) if not isinstance(
                    block[0], dict
                ) else block
                continue

            if key in ("steps", "variables"):
                data[key] = []
                list_key = key
            else:
                data[key] = _coerce(value)
            i += 1
            continue

        i += 1

    return data


def _coerce(value):
    """Convierte un string de frente: bool/int o lo deja como texto."""
    v = value.strip()
    if v == "true":
        return True
    if v == "false":
        return False
    # Comillas dobles/simples: quitar si está entre comillas
    if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
        v = v[1:-1]
    try:
        return int(v)
    except ValueError:
        pass
    return v


def parse_skill_markdown(text):
    """Parsea un archivo SKILL.md y devuelve un Skill (o lanza ValueError)."""
    if not isinstance(text, str):
        raise ValueError("skill_must_be_text")

    lines = text.splitlines()

    if not lines or lines[0].strip() != "---":
        raise ValueError("missing_frontmatter")

    end = None
    for j in range(1, len(lines)):
        if lines[j].strip() == "---":
            end = j
            break

    if end is None:
        raise ValueError("unterminated_frontmatter")

    header = _parse_header(lines[1:end])
    body = "\n".join(lines[end + 1:])

    name = str(header.get("name", "")).strip()
    if not name:
        raise ValueError("skill_name_required")

    description = header.get("description", "").strip() or name

    variables = [
        str(v)
        for v in header.get("variables", [])
        if str(v).strip()
    ]

    steps = []
    for raw_step in header.get("steps", []):
        if not isinstance(raw_step, dict):
            continue

        step = SkillStep(kind="tool", note=raw_step.get("note", ""))

        sub_skill = raw_step.get("skill")
        if sub_skill:
            step.kind = "skill"
            step.skill = str(sub_skill)
            steps.append(step)
            continue

        if raw_step.get("synthesize") is True:
            step.kind = "synthesize"
            step.synthesize = True
            steps.append(step)
            continue

        tool = raw_step.get("tool")
        if tool:
            step.tool = str(tool)
            args = raw_step.get("args") or {}
            step.args = {
                str(k): str(v) for k, v in args.items()
            }
            steps.append(step)
            continue

        # paso sin tool/skill/synthesize no es ejecutable
        raise ValueError(
            "step_without_action: %s" % raw_step
        )

    return Skill(
        name=name,
        description=description,
        steps=steps,
        variables=variables,
        raw=body,
    )


# ----------------------------------------------------------------------
# SkillManager — descubrimiento y ejecución gateada
# ----------------------------------------------------------------------

_DEFAULT_DEPTH = 3


class SkillManager:
    """
    Catálogo y ejecución de skills.

    `run_tool` es un callable inyectado por el núcleo: recibe un ToolCall y
    lo ejecuta a través de la compuerta (SecurityGate + AuditLog). El
    manager NUNCA toca Authority ni GrantStore → ninguna skill se
    autoconcede permisos.

    `synthesize` es un callable opcional para los pasos `synthesize: true`
    (suele ser `provider.generate`); genera texto con el material, no tools.
    """

    def __init__(self, directory=None, run_tool=None, synthesize=None):
        self.directory = directory or "app/skills_catalog"
        self.run_tool = run_tool
        self.synthesize = synthesize or (
            lambda prompt: {
                "success": False,
                "error": "synthesize_not_available",
            }
        )
        self._catalog = {}
        self.discover()

    # -- descubrimiento -------------------------------------------------

    def discover(self):
        """(Re)carga el catálogo desde los .md del directorio."""
        new_catalog = {}

        if os.path.isdir(self.directory):
            for fname in sorted(os.listdir(self.directory)):
                if not fname.endswith(".md"):
                    continue

                path = os.path.join(self.directory, fname)

                try:
                    with open(path, encoding="utf-8") as fh:
                        text = fh.read()
                except OSError:
                    continue

                try:
                    skill = parse_skill_markdown(text)
                except ValueError:
                    # Una skill inválida se omite (fail-closed: no se
                    # ofrece algo que no se puede ejecutar con garantías).
                    continue

                new_catalog[skill.name] = skill

        self._catalog = new_catalog
        return len(new_catalog)

    def catalog(self):
        """Lista pública del catálogo (para UI y detección NL)."""
        return [
            skill.to_dict()
            for skill in sorted(
                self._catalog.values(),
                key=lambda s: s.name,
            )
        ]

    def get(self, name):
        return self._catalog.get(name)

    def has(self, name):
        return name in self._catalog

    # -- ejecución ------------------------------------------------------

    def run(self, name, variables=None, actor="ilu"):
        """
        Ejecuta una skill gateada y devuelve su resultado + traza.

        - Cada paso `tool` se ejecuta vía `self.run_tool(...)` (la
          compuerta decide; puede pedir autorización).
        - Cada paso `skill` resuelve la sub-skill con límite de
          profundidad y guard anti-ciclos.
        - Cada paso `synthesize` llama al proveedor con el material.
        Un fallo en cualquier paso aborta la skill (fail-closed).
        """
        skill = self.get(name)

        if skill is None:
            return {
                "success": False,
                "error": "skill_not_found",
                "name": name,
            }

        scope = {
            "skill": name,
            "actor": actor,
            "material": [],
        }

        try:
            result = self._run_steps(
                name,
                skill.steps,
                scope,
                variables or {},
                depth=0,
                visited=None,
            )
        except _SkillAbort as abort:
            return abort.payload

        steps_log = [
            {"kind": s.kind, "note": s.note, **scope.get("last_tool", {})}
            for s in skill.steps
        ]

        return {
            "success": True,
            "name": name,
            "description": skill.description,
            "steps": steps_log,
            "summary": result,
            "audited": True,
        }

    def _run_steps(
        self,
        root, steps, scope, variables, depth, visited
    ):
        material = scope["material"]
        called = set()

        for step in steps:
            if step.kind == "tool":
                call_out = self._run_tool_step(
                    root, step, scope, variables, material
                )
                if not call_out.get("success"):
                    # fail-closed: abortar la skill con el detalle
                    raise _SkillAbort({
                        "success": False,
                        "name": root,
                        "error": "tool_step_failed",
                        "tool": step.tool,
                        "detail": call_out,
                        "audited": True,
                    })
                material.append(call_out)
                scope["last_tool"] = {
                    "tool": step.tool,
                    "success": True,
                }
                continue

            if step.kind == "synthesize":
                prompt, count = self._synthesis_prompt(
                    root, step.note, material, variables
                )
                synth = self._safe_generate(prompt, count)
                if not synth.get("success"):
                    raise _SkillAbort({
                        "success": False,
                        "name": root,
                        "error": "synthesize_failed",
                        "detail": synth,
                        "audited": True,
                    })
                material.append(synth)
                continue

            if step.kind == "skill":
                if depth + 1 > _DEFAULT_DEPTH:
                    raise _SkillAbort({
                        "success": False,
                        "name": root,
                        "error": "skill_depth_exceeded",
                        "skill": step.skill,
                        "audited": True,
                    })

                visited = visited or set()
                if step.skill in visited:
                    raise _SkillAbort({
                        "success": False,
                        "name": root,
                        "error": "skill_cycle",
                        "skill": step.skill,
                        "audited": True,
                    })

                if step.skill == root:
                    raise _SkillAbort({
                        "success": False,
                        "name": root,
                        "error": "skill_self_reference",
                        "audited": True,
                    })

                sub = self.get(step.skill)
                if sub is None:
                    raise _SkillAbort({
                        "success": False,
                        "name": root,
                        "error": "skill_not_found",
                        "skill": step.skill,
                        "audited": True,
                    })

                visited = set(visited)
                visited.add(root)
                sub_out = self._run_steps(
                    step.skill,
                    sub.steps,
                    scope,
                    variables,
                    depth + 1,
                    visited,
                )
                if not sub_out.get("success"):
                    raise _SkillAbort(sub_out)
                material.append(sub_out)
                continue

        # El resultado visible de la skill es el último material
        if not material:
            return {
                "success": True,
                "response": "La skill «{}» no produjo material.".format(root),
            }

        return material[-1]

    def _run_tool_step(self, root, step, scope, variables, material):
        """Materializa los argumentos y los gatea por `run_tool`."""
        if self.run_tool is None:
            return {
                "success": False,
                "error": "gate_not_injected",
                "tool": step.tool,
            }

        resolved, ok = self._materialize(
            step.args, variables, material
        )

        if not ok:
            return {
                "success": False,
                "error": "variable_unresolved",
                "tool": step.tool,
            }

        tool_call = ToolCall(
            tool=step.tool,
            arguments=resolved,
            reason=step.note or "skill %s" % root,
        )

        return self.run_tool(tool_call)

    def _materialize(self, args, variables, material):
        """Interpola {var} desde variables y resultados de pasos previos."""
        resolved = {}
        env = dict(variables)

        for item in material[-3:]:
            content = (
                item.get("content")
                if isinstance(item, dict)
                else None
            )
            if content:
                env.setdefault("_material%d" % len(material), content)
                env["material"] = content

        for key, templ in args.items():
            if "{" not in templ:
                resolved[key] = templ
                continue
            try:
                resolved[key] = templ.format_map(
                    _SafeDict(env)
                )
            except (KeyError, ValueError):
                return None, False
        return resolved, True

    def _synthesis_prompt(self, root, note, material, variables):
        """Arma el prompt de síntesis con el material recolectado."""
        parts = []
        for item in material:
            content = (
                item.get("content")
                if isinstance(item, dict)
                else str(item)
            )
            text = (
                str(content).strip()
                if content is not None
                else str(item)
            )
            if text and text not in ("None", "{}"):
                parts.append(text)
        prompt = (
            "Sintetiza en español, de forma clara y breve, esto:\n"
            "Intención de la skill: %s\n"
            "Material recolectado:\n%s"
            % (note or root, "\n\n".join(parts[-8:]))
        )
        return prompt, len(parts)

    def _safe_generate(self, prompt, count):
        try:
            if count == 0:
                return {
                    "success": False,
                    "error": "no_material",
                }
            out = self.synthesize(prompt)
        except Exception as error:
            return {
                "success": False,
                "error": "synthesize_failed",
                "detail": str(error),
            }
        if isinstance(out, dict):
            return out
        return {
            "success": True,
            "content": str(out),
        }


class _SkillAbort(Exception):
    """Aborta la ejecución con un resultado controlado (fail-closed)."""

    def __init__(self, payload):
        super().__init__(payload.get("error", "skill_aborted"))
        self.payload = payload


class _SafeDict(dict):
    """dict que devuelve su propio par para no lanzar en format_map."""

    def __missing__(self, key):
        return "{" + key + "}"


def create_skill_manager(run_tool=None, synthesize=None, directory=None):
    """Factory: manager con el catálogo por defecto."""
    return SkillManager(
        directory=directory,
        run_tool=run_tool,
        synthesize=synthesize,
    )