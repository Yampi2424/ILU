---
name: aprende-sobre
description: >-
  Lee material (archivo o tema) e incorpora lo aprendido a la memoria
  semántica de I.L.U. para que lo recuerde después.
variables:
  - fuente
  - tema
steps:
  - tool: read_file
    args:
      path: "{fuente}"
    note: "Leyendo la fuente…"
  - tool: web_search
    args:
      query: "{tema}"
      limit: 3
    note: "Ampliando con contexto…"
  - tool: memory_ingest
    args:
      source: "{fuente}"
      tag: learn
    note: "Incorporo lo aprendido a mi memoria semántica."
---
# Skill: aprende-sobre

Ciclo de aprendizaje asistido: lee, amplía y escribe en la memoria
semántica de I.L.U. (`memory_ingest`, permission `safe`, scope de
workspace). El paso de escritura de memoria es inofensivo (su propia
memoria) y no requiere autorización elevada. Ningún paso concede permisos.