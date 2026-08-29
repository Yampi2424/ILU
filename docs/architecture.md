# Arquitectura de I.L.U.

## Objetivo

I.L.U. será un asistente inteligente diseñado con una arquitectura orientada a la nube.

## Principios

- El computador local será solamente un cliente.
- El procesamiento principal estará en la nube.
- El código estará versionado mediante Git.
- La memoria estará separada del código.
- Los modelos de IA serán intercambiables.
- Las credenciales y secretos nunca se almacenarán en Git.
- La arquitectura deberá poder migrarse entre proveedores cloud.

## Componentes

### App
Punto de entrada de la aplicación.

### Core
Lógica principal y coordinación de I.L.U.

### Memory
Sistema de memoria persistente.

### Models
Integración con modelos de inteligencia artificial.

### Tools
Herramientas que I.L.U. podrá utilizar.

### Config
Configuraciones no sensibles.

### Tests
Pruebas automáticas.

### Docs
Documentación del proyecto.
