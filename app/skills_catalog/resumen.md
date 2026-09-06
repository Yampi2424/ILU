---
name: resumen
description: >-
  Sintetiza el contenido de un archivo del workspace o de resultados de
  búsqueda web en un resumen breve y claro en español.
variables:
  - fuente
steps:
  - tool: read_file
    args:
      path: "{fuente}"
    note: "Leyendo la fuente indicada…"
  - synthesize: true
    note: "Sintetizo el material en un resumen."
---
# Skill: resumen

Genera un resumen accesible de un documento local. La lectura atraviesa la
compuerta de seguridad (`read_file`); la síntesis la redacta el proveedor
con el material recolectado como contexto. Ningún paso concede permisos.