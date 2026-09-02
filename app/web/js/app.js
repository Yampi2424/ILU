/**
 * I.L.U. — Punto de entrada de la interfaz
 *
 * Conecta el cliente API, el componente visual del Corazón,
 * y la interfaz de usuario. Gestiona el flujo de conversación.
 *
 * PRINCIPIO: este módulo SOLO orquesta las capas visuales
 * y transmite órdenes al backend. NUNCA decide permisos
 * ni ejecuta autoridad — esa responsabilidad es del núcleo
 * Python (ILUCore + Authority + SecurityGate).
 */

(function () {
  'use strict';

  let _sessionId = 'web-' + Date.now();
  let _sending = false;

  // --- Inicialización -----------------------------------------------

  function init() {
    ILUCore.init();
    _bindEvents();
    _loadSecurityState();
    _pollAuthorizationRequests();
  }

  function _bindEvents() {
    // Navegación
    document.querySelectorAll('.topbar-btn[data-view]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        ILUUI.switchView(btn.getAttribute('data-view'));
      });
    });

    document.getElementById('sidebarToggle').addEventListener('click', function () {
      ILUUI.toggleSidebar();
    });

    // Chat
    var input = document.getElementById('chatInput');
    var sendBtn = document.getElementById('chatSend');

    sendBtn.addEventListener('click', _sendMessage);

    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        _sendMessage();
      }
    });

    // Auto-resize del textarea
    input.addEventListener('input', function () {
      this.style.height = 'auto';
      this.style.height = Math.min(this.scrollHeight, 120) + 'px';
    });

    // Panel modal
    document.getElementById('panelClose').addEventListener('click', ILUUI.closePanel);
    document.getElementById('panelOverlay').addEventListener('click', function (e) {
      if (e.target === this) ILUUI.closePanel();
    });

    // Autonomía
    document.querySelectorAll('#autonomyButtons .action-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        ILUUI.changeAutonomy(btn.getAttribute('data-level'));
      });
    });
  }

  // --- Seguridad ----------------------------------------------------

  async function _loadSecurityState() {
    var data = await ILUApi.security();

    if (data.error) return;

    ILUUI.updateModeBadge(data.autonomy || 'manual');

    // Si hay emergencia activa, mostrar estado
    if (data.emergency_active && data.emergency_active.length > 0) {
      ILUCore.set(ILUCore.STATES.EMERGENCY);
    }

    // Si hay solicitudes abiertas, entrar en estado de espera
    if (data.authorization_requests_open && data.authorization_requests_open > 0) {
      ILUCore.set(ILUCore.STATES.AUTHORIZATION);
    }
  }

  /**
   * Poll periódico para solicitudes de autorización abiertas.
   * Si hay alguna, el Corazón entra en estado de espera.
   */
  function _pollAuthorizationRequests() {
    setInterval(async function () {
      var data = await ILUApi.authorizationRequests();

      if (data.error) return;

      var openRequests = (data.requests || []).filter(function (r) {
        return r.status === 'open' || r.status === 'pending';
      });

      if (openRequests.length > 0 && ILUCore.isIdle()) {
        ILUCore.set(ILUCore.STATES.AUTHORIZATION);
      }
    }, 15000);
  }

  // --- Conversación -------------------------------------------------

  async function _sendMessage() {
    var input = document.getElementById('chatInput');
    var message = input.value.trim();

    if (!message || _sending) return;

    _sending = true;

    var sendBtn = document.getElementById('chatSend');
    sendBtn.disabled = true;
    input.value = '';
    input.style.height = 'auto';

    // Mostrar el mensaje del usuario
    ILUUI.appendMessage('user', message);

    // Estado: escuchando → pensando
    ILUCore.showListening();

    setTimeout(function () {
      ILUCore.showThinking();
    }, 400);

    ILUUI.showTypingIndicator();

    // Enviar al backend
    var result = await ILUApi.ask(message, _sessionId);

    ILUUI.removeTypingIndicator();

    // Aplicar estado del Corazón según la respuesta
    ILUCore.applyFromResponse(result);

    if (result.error && !result.response) {
      // Error de red o del servidor
      ILUUI.appendMessage(
        'assistant',
        'Error: ' + (result.error === 'network_error'
          ? 'No se pudo conectar con I.L.U.'
          : result.error),
        'ERROR'
      );
    } else {
      // Respuesta de I.L.U.
      var responseText = result.response || 'Sin respuesta.';

      var meta = (result.intent || '').toUpperCase();
      if (result.tool) meta += (meta ? ' · ' : '') + result.tool;
      if (result.provider) {
        meta += (meta ? ' · ' : '') + result.provider.name;
        if (result.provider.fallback) meta += ' (fallback)';
      }

      ILUUI.appendMessage('assistant', responseText, meta);
    }

    // Actualizar sidebar
    ILUUI.updateSidebarContext(result.context);
    ILUUI.updateSidebarTool(result.tool, result.tool_result);
    ILUUI.updateSidebarSubagent(result.subagent);
    ILUUI.updateSidebarProvider(result.provider);

    // Si la respuesta indica que falta autorización, mostrarla
    if (result.authorization === 'ask' || result.authorization_request_id) {
      var reqId = result.authorization_request_id || '';
      ILUUI.appendMessage(
        'assistant',
        'Solicitud de autorización abierta. Ve a la pestaña Permisos para concederla o denegarla.'
          + (reqId ? ' ID: ' + reqId.substring(0, 8) : ''),
        'AUTHORIZATION'
      );
    }

    _sending = false;
    sendBtn.disabled = false;
    input.focus();
  }

  // --- Arranque -----------------------------------------------------

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
