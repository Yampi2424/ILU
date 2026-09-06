---
name: organizar-archivos
description: >-
  Inspecciona el workspace y reorganiza archivos por tipo en subcarpetas
  con nombres claros. Pide autorización para cada escritura.
variables:
  - carpeta
steps:
  - tool: read_file
    args:
      path: "{carpeta}"
    note: "Inspeccionando la carpeta indicada…"
  - tool: write_file
    args:
      path: "{carpeta}/organizado/README.md"
      content: "Carpeta organizada por I.L.U."
    note: "Creando la estructura de destino…"
---
# Skill: organizar-archivos

Organización asistida del workspace. El paso de inspección es de lectura
(`read_file`); los pasos de escritura usan `write_file`, que la
configuración marca como `ask`: si no existe un grant durable del owner,
la compuerta abre una solicitud de autorización en lugar de ejecutar.
Así la skill **nunca** decide por sí sola mover o escribir archivos.