# Catálogo de Skills de I.L.U.

Cada skill es un archivo Markdown con un frontmatter `---` de cabecera y un
cuerpo de **pasos**. Los pasos encadenan `tool` (herramientas registradas y
gateadas) o `skill` (sub-skills), resolviendo plantillas `{variable}` desde
las variables de invocación y desde la salida de pasos anteriores.

Formato (tolerante por diseño; el parser conserva campos desconocidos):

```markdown
---
name: <slug único>
description: <qué hace, para el detección por lenguaje natural>
steps:
  - tool: <herramienta registrada>
    args:
      clave: "valor o plantilla {var}"
    note: "resumen en lenguaje natural para la UI"
  - skill: <slug de otra skill>
    note: "paso que reutiliza otra skill"
---
```

Reglas de ejecución (ver `app/skills.py`):

- **Las skills NUNCA se conceden permisos.** Cada paso atraviesa la misma
  compuerta (SecurityGate) que una tool propuesta por el modelo, con
  `mode="skill"`, y se audita (`tool_attempt`/`tool_result`).
- Una tool no registrada o denegada por la compuerta **no se ejecuta**
  (fail-closed); si la compuerta abre una solicitud de autorización, esa
  solicitud la resuelve el owner.
- Las sub-skills se resuelven con límite de profundidad y guard anti-ciclos.
- Las variables `{var}` se interpolan con valores escapados seguros (nunca
  directo a shell; las tools reciben argumentos estructurados).