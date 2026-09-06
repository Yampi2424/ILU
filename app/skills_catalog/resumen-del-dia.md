---
name: resumen-del-dia
description: >-
  Arma un digest del día a partir del estado que inyecta el orquestador:
  objetivos activos, tareas pendientes, avisos y aprendizajes recientes.
variables:
  - estado_dia
steps:
  - synthesize: true
    note: "Compongo el digest del día…"
  - tool: notify
    args:
      message: "Preparado resumen del día."
    note: "Te aviso cuando esté listo."
---
# Skill: resumen-del-dia

Digest personal de I.L.U. usado por el scheduler (job `digest`). El
orquestador inyecta el estado del día (objetivos, tareas, avisos,
aprendizajes) en `{estado_dia}`; la skill lo condensa en lenguaje natural
y notifica al owner. Sin pasos de permisos elevados.