---
name: investigacion
description: >-
  Investiga un tema en profundidad: consulta web por sub-cuestiones y
  entrega un informe con fuentes.
variables:
  - tema
steps:
  - tool: web_search
    args:
      query: "{tema}"
      limit: 8
    note: "Consultando la web sobre «{tema}»…"
  - synthesize: true
    note: "Compongo el informe con las fuentes encontradas."
---
# Skill: investigacion

Pipeline de investigación ligera: búsqueda web + síntesis estructurada
(informe con sección de fuentes). Cada `web_search` se ejecuta vía la
compuerta y se audita; la síntesis final la redacta el proveedor con el
material recolectado como contexto.