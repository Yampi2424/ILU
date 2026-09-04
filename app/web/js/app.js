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
  let _voiceEngine = null;   // 'realtime' | 'legacy' | null

  // --- Inicialización -----------------------------------------------

  function init() {
    ILUCore.init();
    _initVoice();
    _bindEvents();
    _loadSecurityState();
    _pollAuthorizationRequests();
  }

  /**
   * Prefiere el motor de voz EN TIEMPO REAL (realtime.js): micrófono
   * real + VAD + visualización dual + TTS del backend + barge-in. Si el
   * navegador no lo soporta (sin getUserMedia/AudioContext/Web Speech),
   * cae al motor legado (voice.js, Web Speech puro).
   */
  function _initVoice() {
    if (window.ILURealtime && ILURealtime.init()) {
      ILURealtime.setCallbacks({
        onUtterance: _sendVoiceText,
        onInterim: _onLiveTranscript,
        onListening: _onRealtimeListening,
        onCapturing: _onCapturing,
        onSpeaking: _onSpeaking,
        onBargeIn: _onBargeIn,
        onModeChange: _onVoiceModeChange,
        onError: _onVoiceError,
        onUnavailable: _onVoiceUnavailable
      });
      _voiceEngine = 'realtime';
      return;
    }

    // Fallback: motor legado (Web Speech para STT y TTS).
    if (window.ILUVoice) {
      ILUVoice.init();
      ILUVoice.configure({ onTranscript: _sendVoiceText });
      ILUVoice.setCallbacks({
        onInterim: _onLiveTranscript,
        onTranscribed: _onLiveTranscript,
        onListening: _onVoiceListening,
        onError: _onVoiceError,
        onUnavailable: _onVoiceUnavailable,
        onModeChange: _onVoiceModeChange
      });

      _voiceEngine = 'legacy';

      if (!ILUVoice.isAvailable()) {
        _onVoiceUnavailable();
      }
    }
  }

  function _voiceActive() {
    if (_voiceEngine === 'realtime') return ILURealtime.isActive();
    if (_voiceEngine === 'legacy') return ILUVoice && ILUVoice.isActive();
    return false;
  }

  function _engineSpeak(text) {
    if (_voiceEngine === 'realtime') ILURealtime.speakResponse(text);
    else if (_voiceEngine === 'legacy') ILUVoice.speakResponse(text);
  }

  function _toggleVoice() {
    if (_voiceEngine === 'realtime') {
      if (ILURealtime.isActive()) {
        ILURealtime.stop();
        _setMicUI('idle');
      } else {
        ILURealtime.start();
      }
      return;
    }
    if (_voiceEngine === 'legacy' && window.ILUVoice) {
      window.ILUVoice.toggle();
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
      micBtn.addEventListener('click', _toggleVoice);
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
    // El mensaje "en vivo" se consolida en un mensaje real de usuario.
    ILUUI.clearLiveUserMessage();
    if (_sending) {
      // Ya hay una petición en curso: no encolar por voz.
      if (_voiceEngine === 'legacy' && window.ILUVoice) window.ILUVoice.cancelTurn();
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

    var isVoice = _voiceActive();

    _sending = true;

    var sendBtn = document.getElementById('chatSend');
    if (sendBtn) sendBtn.disabled = true;

    var input = document.getElementById('chatInput');
    if (input) { input.value = ''; input.style.height = 'auto'; }

    // Mostrar el mensaje del usuario
    ILUUI.appendMessage('user', message);

    // Estado: escuchando → pensando
    if (!isVoice) {
      ILUCore.showListening();
      setTimeout(function () {
        ILUCore.showThinking();
      }, 400);
    } else {
      ILUCore.showThinking();
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
    if (!isVoice || !_voiceEngine) {
      ILUCore.applyFromResponse(result);
      return;
    }

    if (result.error && !result.response) {
      ILUCore.set(ILUCore.STATES.ERROR);
      setTimeout(function () { ILUCore.showIdle(); }, 4000);
      if (_voiceEngine === 'legacy') window.ILUVoice.speakError();
      else if (window.ILURealtime) window.ILURealtime.finishTurn();
    } else if (result.authorization === 'ask') {
      ILUCore.set(ILUCore.STATES.AUTHORIZATION);
      if (_voiceEngine === 'legacy') window.ILUVoice.speakAuthorization();
      else if (window.ILURealtime) window.ILURealtime.finishTurn();
    } else if (result.tool) {
      ILUCore.set(ILUCore.STATES.WORKING);
      setTimeout(function () {
        ILUCore.set(ILUCore.STATES.RESPONDING);
        _engineSpeak(responseText);
      }, 250);
    } else {
      ILUCore.set(ILUCore.STATES.RESPONDING);
      _engineSpeak(responseText);
    }
  }

  // --- UI de voz ----------------------------------------------------

  /** Lo que I.L.U. escucha mientras hablas → mensaje vivo en el chat. */
  function _onLiveTranscript(text) {
    if (!text) return;
    ILUUI.liveUserMessage(text);
  }

  function _onVoiceListening(on) {
    _setMicUI(on ? 'listening' : 'idle');
  }

  function _onVoiceModeChange(on) {
    _setMicUI(on ? 'listening' : 'idle');
    // Modo voz: I.L.U. presente como centro; la interfaz se repliega.
    document.body.classList.toggle('voice-mode', on);
    if (on) ILUCore.showListening();
    else ILUCore.showIdle();
  }

  function _onVoiceError(err) {
    ILUUI.appendMessage('assistant', 'Error de voz: ' + err, 'VOZ');
    ILUCore.set(ILUCore.STATES.ERROR);
    setTimeout(function () { ILUCore.showIdle(); }, 2500);
  }

  function _onVoiceUnavailable(reason) {
    if (reason === 'mic_permission') {
      ILUUI.appendMessage(
        'assistant',
        'Concede acceso al micrófono para poder conversar conmigo.',
        'VOZ'
      );
      _setMicUI('idle');
    } else {
      ILUUI.appendMessage(
        'assistant',
        'La voz no está disponible en este navegador.',
        'VOZ'
      );
      // Solo se desactiva el botón cuando la voz es genuinamente
      // insoportada (motor legado); un permiso denegado es reversible.
      if (_voiceEngine !== 'realtime') {
        var micBtn = document.getElementById('micButton');
        if (micBtn) micBtn.disabled = true;
      }
    }
  }

  // --- Callbacks del motor REAL-TIME (realtime.js) ------------------

  function _onRealtimeListening(on) {
    _setMicUI(on ? 'listening' : 'idle');
  }

  /** El usuario está hablando (VAD): resalta el micrófono y el plasma. */
  function _onCapturing(on) {
    _setMicUI(on ? 'live' : 'listening');
    if (on) ILUCore.showListening();
  }

  /** I.L.U. está reproduciendo su respuesta: la presencia "cobra voz". */
  function _onSpeaking(on) {
    _setMicUI(on ? 'speaking' : 'listening');
    if (on) ILUCore.set(ILUCore.STATES.RESPONDING);
    else ILUCore.showListening();
  }

  /** Barge-in: el usuario interrumpió a I.L.U.; vuelve a escucharla. */
  function _onBargeIn() {
    ILUCore.showListening();
  }

  function _setMicUI(state) {
    var micBtn = document.getElementById('micButton');
    if (!micBtn) return;
    micBtn.classList.remove('active', 'speaking', 'live');
    if (state === 'listening' || state === 'active') micBtn.classList.add('active');
    else if (state === 'live') micBtn.classList.add('active', 'live');
    else if (state === 'speaking') micBtn.classList.add('speaking');
  }

  // --- Arranque -----------------------------------------------------

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
