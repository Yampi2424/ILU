/**
 * I.L.U. — Punto de entrada de la interfaz
 *
 * Conecta el cliente API, el componente visual del Corazón,
 * la interfaz de usuario y la capa de voz. Gestiona el flujo de
 * conversación (texto y voz).
 *
 * PRINCIPIO: este módulo SOLO orquesta las capas visuales y transmite
 * órdenes al backend. NUNCA decide permisos ni ejecuta autoridad — esa
 * responsabilidad es del núcleo Python (ILUCore + Authority +
 * SecurityGate). La voz (ILUVoice) produce texto y reproduce texto por
 * el MISMO /ask, con las mismas reglas: nunca es un bypass.
 */

(function () {
  'use strict';

  let _sessionId = 'web-' + Date.now();
  let _sending = false;

  // --- Inicialización -----------------------------------------------

  function init() {
    ILUCore.init();
    _initVoice();
    _bindEvents();
    _loadSecurityState();
    _pollAuthorizationRequests();
  }

  function _initVoice() {
    if (!window.ILUVoice) return;

    ILUVoice.init();
    ILUVoice.configure({ onTranscript: _sendVoiceText });
    ILUVoice.setCallbacks({
      onTranscribed: _showVoiceTranscript,
      onListening: _onVoiceListening,
      onError: _onVoiceError,
      onUnavailable: _onVoiceUnavailable,
      onModeChange: _onVoiceModeChange
    });

    // Si el navegador no soporta voz, desactivar el botón.
    if (!ILUVoice.isAvailable()) {
      _onVoiceUnavailable();
    }
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

    // Micrófono (voz)
    var micBtn = document.getElementById('micButton');
    if (micBtn) {
      micBtn.addEventListener('click', function () {
        if (window.ILUVoice) window.ILUVoice.toggle();
      });
    }

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

  // --- Conversación (texto) ----------------------------------------

  function _sendMessage() {
    var input = document.getElementById('chatInput');
    var message = input.value.trim();
    if (!message) return;
    _dispatchMessage(message);
  }

  // --- Conversación (voz) ------------------------------------------

  /**
   * La voz entrega texto y lo despacha por el MISMO camino que el
   * texto escrito. No es un pipeline distinto.
   */
  function _sendVoiceText(text) {
    if (!text) return;
    if (_sending) {
      // Ya hay una petición en curso: no encolar por voz.
      if (window.ILUVoice) window.ILUVoice.cancelTurn();
      return;
    }
    _dispatchMessage(text);
  }

  /**
   * Flujo de conversación compartido (texto y voz): construye el
   * mensaje de usuario, consulta /ask, aplica el estado visual y, si
   * la voz está activa, habla la respuesta. El texto pasa por el mismo
   * /ask → ILUCore → memoria → proveedor → SecurityGate/Tools/Tasks.
   */
  async function _dispatchMessage(message) {
    if (!message || _sending) return;

    var isVoice = window.ILUVoice && window.ILUVoice.isActive();

    _sending = true;

    var sendBtn = document.getElementById('chatSend');
    if (sendBtn) sendBtn.disabled = true;

    var input = document.getElementById('chatInput');
    if (input) { input.value = ''; input.style.height = 'auto'; }

    // Mostrar el mensaje del usuario
    ILUUI.appendMessage('user', message);

    // Estado: escuchando → pensando (solo texto; en voz lo gestiona ILUVoice)
    if (!isVoice) {
      ILUCore.showListening();
      setTimeout(function () {
        ILUCore.showThinking();
      }, 400);
    }

    ILUUI.showTypingIndicator();

    // Enviar al backend (el MISMO /ask)
    var result = await ILUApi.ask(message, _sessionId);

    ILUUI.removeTypingIndicator();

    var responseText = result.response || 'Sin respuesta.';

    // Aplicar estado visual y hablar la respuesta (si voz activa)
    _applyVisualAndSpeak(result, responseText, isVoice);

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
    if (sendBtn) sendBtn.disabled = false;
    if (input) input.focus();
  }

  /**
   * Aplica el estado visual según la respuesta. Si la voz está activa,
   * habla la respuesta (la voz gestiona responding → idle); si no, usa
   * el mapeo estándar de ILUCore.applyFromResponse.
   *
   * La seguridad NO cambia: la voz solo reproduce el texto de la misma
   * respuesta; nunca concede permisos ni altera el flujo.
   */
  function _applyVisualAndSpeak(result, responseText, isVoice) {
    if (!isVoice || !window.ILUVoice) {
      ILUCore.applyFromResponse(result);
      return;
    }

    if (result.error && !result.response) {
      ILUCore.set(ILUCore.STATES.ERROR);
      setTimeout(function () { ILUCore.showIdle(); }, 4000);
      window.ILUVoice.speakError();
    } else if (result.authorization === 'ask') {
      ILUCore.set(ILUCore.STATES.AUTHORIZATION);
      window.ILUVoice.speakAuthorization();
    } else if (result.tool) {
      ILUCore.set(ILUCore.STATES.WORKING);
      setTimeout(function () {
        window.ILUVoice.speakResponse(responseText);
      }, 250);
    } else {
      window.ILUVoice.speakResponse(responseText);
    }
  }

  // --- UI de voz ----------------------------------------------------

  function _showVoiceTranscript(text) {
    var el = document.getElementById('voiceTranscript');
    if (el) el.textContent = text;
    _refreshVoiceBar();
  }

  function _onVoiceListening(on) {
    _setMicUI(on ? 'listening' : 'idle');
    _setVoiceStatus(on ? 'Escuchando…' : '');
  }

  function _onVoiceModeChange(on) {
    _setMicUI(on ? 'active' : 'idle');
    _setVoiceStatus(on ? 'Voz activa — habla para conversar' : '');
  }

  function _onVoiceError(err) {
    _setVoiceStatus('Error de voz: ' + err);
    ILUCore.set(ILUCore.STATES.ERROR);
    setTimeout(function () { ILUCore.showIdle(); }, 2500);
  }

  function _onVoiceUnavailable() {
    _setVoiceStatus('La voz no está disponible en este navegador');
    var micBtn = document.getElementById('micButton');
    if (micBtn) micBtn.disabled = true;
    _refreshVoiceBar();
  }

  function _setMicUI(state) {
    var micBtn = document.getElementById('micButton');
    if (!micBtn) return;
    micBtn.classList.remove('active', 'speaking');
    if (state === 'listening') micBtn.classList.add('active');
    else if (state === 'active' || state === 'speaking') micBtn.classList.add('speaking');
  }

  function _setVoiceStatus(text) {
    var status = document.getElementById('voiceStatus');
    if (status) status.textContent = text;
    _refreshVoiceBar();
  }

  function _refreshVoiceBar() {
    var bar = document.getElementById('voiceBar');
    if (!bar) return;
    var status = document.getElementById('voiceStatus');
    var transcript = document.getElementById('voiceTranscript');
    var hasStatus = status && status.textContent;
    var hasTrans = transcript && transcript.textContent;
    bar.hidden = !(hasStatus || hasTrans);
  }

  // --- Arranque -----------------------------------------------------

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
