/**
 * I.L.U. — Punto de entrada de la interfaz inmersiva
 *
 * Conecta el cliente API, el componente visual del Corazón,
 * la interfaz de usuario (dock + drawers), y la capa de voz.
 * Flujo: streaming por SSE → fallback a /ask síncrono.
 *
 * PRINCIPIO: este módulo SOLO orquesta capas visuales y transmite
 * órdenes al backend. NUNCA decide permisos ni ejecuta autoridad.
 */

(function () {
  'use strict';

  let _sessionId = 'web-' + Date.now();
  let _sending = false;
  let _voiceEngine = null;
  let _currentPanel = null;
  let _pinResolve = null;  // Promise resolver for PIN modal

  // --- Inicialización -----------------------------------------------

  function init() {
    ILUCore.init();
    _initVoice();
    _bindEvents();
    _loadSecurityState();
    _pollAuthorizationRequests();
    _pollPresence();
    _restoreHistory();
  }

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
      if (!ILUVoice.isAvailable()) _onVoiceUnavailable();
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
        ILURealtime.stop(); _setMicUI('idle');
      } else { ILURealtime.start(); }
      return;
    }
    if (_voiceEngine === 'legacy' && window.ILUVoice) window.ILUVoice.toggle();
  }

  function _bindEvents() {
    // Dock navigation
    document.querySelectorAll('.dock-item[data-panel]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        ILUUI.openDrawer(btn.getAttribute('data-panel'));
      });
    });

    document.getElementById('panelDrawerClose').addEventListener('click', ILUUI.closeDrawer);
    document.getElementById('panelDrawerOverlay').addEventListener('click', ILUUI.closeDrawer);

    // Chat
    var input = document.getElementById('chatInput');
    var sendBtn = document.getElementById('chatSend');
    sendBtn.addEventListener('click', _sendMessage);
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); _sendMessage(); }
    });
    input.addEventListener('input', function () {
      this.style.height = 'auto';
      this.style.height = Math.min(this.scrollHeight, 120) + 'px';
    });

    // Micrófono
    var micBtn = document.getElementById('micButton');
    if (micBtn) micBtn.addEventListener('click', _toggleVoice);

    // PIN modal
    document.getElementById('pinModalCancel').addEventListener('click', function () {
      if (_pinResolve) _pinResolve(null);
    });
    document.getElementById('pinModalConfirm').addEventListener('click', function () {
      var pin = document.getElementById('pinInput').value.trim();
      if (_pinResolve) _pinResolve(pin);
    });
    document.getElementById('pinInput').addEventListener('keydown', function (e) {
      if (e.key === 'Enter') document.getElementById('pinModalConfirm').click();
    });
    document.getElementById('pinModalOverlay').addEventListener('click', function (e) {
      if (e.target === this && _pinResolve) _pinResolve(null);
    });
  }

  // --- Seguridad ----------------------------------------------------

  async function _loadSecurityState() {
    var data = await ILUApi.security();
    if (data.error) return;
    ILUUI.updateModeBadge(data.autonomy || 'manual');
    if (data.emergency_active && data.emergency_active.length > 0) ILUCore.set(ILUCore.STATES.EMERGENCY);
    if (data.authorization_requests_open && data.authorization_requests_open > 0) ILUCore.set(ILUCore.STATES.AUTHORIZATION);
  }

  function _pollAuthorizationRequests() {
    setInterval(async function () {
      var data = await ILUApi.authorizationRequests();
      if (data.error) return;
      var open = (data.requests || []).filter(function (r) { return r.status === 'open' || r.status === 'pending'; });
      if (open.length > 0 && ILUCore.isIdle()) ILUCore.set(ILUCore.STATES.AUTHORIZATION);
    }, 15000);
  }

  var _lastNotifTs = '';

  function _pollPresence() {
    setInterval(async function () {
      try {
        var state = await ILUApi.state();
        if (state && !state.error) _renderPresenceDetails(state);
      } catch (_) { /* best-effort */ }
      try {
        var data = await ILUApi.notifications();
        var notifs = (data && data.notifications) || [];
        for (var i = notifs.length - 1; i >= 0; i--) {
          var n = notifs[i];
          if (!n.ts || !n.message) continue;
          if (_lastNotifTs && n.ts <= _lastNotifTs) continue;
          ILUUI.appendMessage('assistant', n.message, 'I.L.U. · aviso');
        }
        if (notifs.length > 0) _lastNotifTs = notifs[0].ts || _lastNotifTs;
      } catch (_) { /* best-effort */ }
    }, 12000);
  }

  function _renderPresenceDetails(awareness) {
    var parts = [];
    if (awareness.goals && awareness.goals.length) parts.push(awareness.goals.length + ' objetivo' + (awareness.goals.length > 1 ? 's' : ''));
    if (awareness.proactive && awareness.proactive.length) parts.push(awareness.proactive.length + ' pendiente' + (awareness.proactive.length > 1 ? 's' : ''));
    if (awareness.preferences && awareness.preferences.length) parts.push('te conozco');
    if (awareness.perception && awareness.perception.length) {
      var sensed = awareness.perception.map(function (p) { return p.summary; }).filter(Boolean);
      if (sensed.length) parts.push(sensed.join(' · '));
    }
    var el = document.getElementById('stateHint');
    if (el) el.textContent = parts.join(' · ');
  }

  // --- Restauración de historial ------------------------------------

  async function _restoreHistory() {
    try {
      var data = await ILUApi.conversations(_sessionId, 50);
      var turns = data.turns || data.messages || null;
      if (data.error || !turns || !turns.length) return;
      var container = document.getElementById('chatMessages');
      if (!container) return;
      container.innerHTML = '';
      turns.forEach(function (t) {
        if (t.role === 'user') ILUUI.appendMessage('user', t.content);
        else if (t.role === 'assistant' || t.role === 'system') ILUUI.appendMessage('assistant', t.content, '');
      });
      container.scrollTop = container.scrollHeight;
    } catch (_) { /* best-effort */ }
  }

  // --- Conversación (texto) ----------------------------------------

  function _sendMessage() {
    var input = document.getElementById('chatInput');
    var message = input.value.trim();
    if (!message) return;
    _dispatchMessage(message);
  }

  // --- Conversación (voz) ------------------------------------------

  function _sendVoiceText(text) {
    if (!text) return;
    ILUUI.clearLiveUserMessage();
    if (_sending) {
      if (_voiceEngine === 'legacy' && window.ILUVoice) window.ILUVoice.cancelTurn();
      return;
    }
    _dispatchMessage(text);
  }

  async function _dispatchMessage(message) {
    if (!message || _sending) return;
    var isVoice = _voiceActive();
    _sending = true;

    var sendBtn = document.getElementById('chatSend');
    if (sendBtn) sendBtn.disabled = true;
    var input = document.getElementById('chatInput');
    if (input) { input.value = ''; input.style.height = 'auto'; }

    ILUUI.appendMessage('user', message);

    if (!isVoice) {
      ILUCore.showListening();
      setTimeout(function () { ILUCore.showThinking(); }, 400);
    } else { ILUCore.showThinking(); }

    ILUUI.showTypingIndicator();

    var assistantMsgId = null;
    var accumulatedText = '';
    var finalResult = null;
    var streamFailed = false;

    var handlers = {
      onStatus: function (state) {
        var map = {
          listening: ILUCore.STATES.LISTENING,
          thinking: ILUCore.STATES.THINKING,
          working: ILUCore.STATES.WORKING,
          responding: ILUCore.STATES.RESPONDING,
          learning: ILUCore.STATES.LEARNING,
          streaming: ILUCore.STATES.STREAMING,
          researching: ILUCore.STATES.RESEARCHING,
          scheduled: ILUCore.STATES.SCHEDULED,
          consolidating: ILUCore.STATES.CONSOLIDATING
        };
        if (map[state]) ILUCore.set(map[state]);
      },
      onToken: function (text) {
        accumulatedText += text;
        if (assistantMsgId) {
          ILUUI.updateMessage(assistantMsgId, accumulatedText);
        } else {
          assistantMsgId = ILUUI.appendMessage('assistant', accumulatedText, '');
        }
        ILUUI.removeTypingIndicator();
        ILUCore.setIntensity(0.6); // pulso mientras llega texto
      },
      onAction: function (event) {
        var kind = event.kind || 'tool';
        var tool = event.tool;
        var args = event.arguments;
        var reason = event.reason;
        var summary = 'Ejecutando ' + tool;
        if (reason) summary += ': ' + reason;
        if (assistantMsgId) ILUUI.updateMessage(assistantMsgId, accumulatedText + '\n\n— ' + summary);
        ILUCore.set(ILUCore.STATES.WORKING);
      },
      onActionResult: function (event) {
        if (assistantMsgId && event.response) {
          ILUUI.updateMessage(assistantMsgId, accumulatedText + '\n\n— ' + event.response);
        }
      },
      onFinal: function (result) {
        finalResult = result;
        ILUUI.removeTypingIndicator();
      },
      onError: function (event) {
        if (event && event.fallback) streamFailed = true;
        else { finalResult = event; ILUUI.removeTypingIndicator(); }
      },
      onDone: function () {}
    };

    await ILUApi.askStream(message, _sessionId, handlers);

    if (streamFailed && !finalResult) {
      await new Promise(function (r) { setTimeout(r, 50); });
    }

    if (!finalResult) {
      finalResult = { error: 'stream_failed', response: 'No se recibió respuesta del stream.' };
    }

    var responseText = finalResult.response || accumulatedText || 'Sin respuesta.';
    _applyVisualAndSpeak(finalResult, responseText, isVoice);

    if (finalResult.error && !finalResult.response && !accumulatedText) {
      ILUUI.appendMessage('assistant', 'Error: ' + (finalResult.error === 'network_error' ? 'No se pudo conectar con I.L.U.' : finalResult.error), 'ERROR');
    } else {
      var meta = (finalResult.intent || '').toUpperCase();
      if (finalResult.tool) meta += (meta ? ' · ' : '') + finalResult.tool;
      if (finalResult.provider) {
        meta += (meta ? ' · ' : '') + finalResult.provider.name;
        if (finalResult.provider.fallback) meta += ' (fallback)';
      }
      if (assistantMsgId) ILUUI.setMessageMeta(assistantMsgId, meta);
      else ILUUI.appendMessage('assistant', responseText, meta);
    }

    ILUUI.updateSidebarContext(finalResult.context);
    ILUUI.updateSidebarTool(finalResult.tool, finalResult.tool_result);
    ILUUI.updateSidebarSubagent(finalResult.subagent);
    ILUUI.updateSidebarProvider(finalResult.provider);

    if (finalResult.authorization === 'ask' || finalResult.authorization_request_id) {
      var reqId = finalResult.authorization_request_id || '';
      ILUUI.appendMessage('assistant', 'Solicitud de autorización abierta. Abre Permisos en el dock para concederla o denegarla.' + (reqId ? ' ID: ' + reqId.substring(0, 8) : ''), 'AUTHORIZATION');
    }

    _sending = false;
    if (sendBtn) sendBtn.disabled = false;
    if (input) input.focus();
  }

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
      setTimeout(function () { ILUCore.set(ILUCore.STATES.RESPONDING); _engineSpeak(responseText); }, 250);
    } else {
      ILUCore.set(ILUCore.STATES.RESPONDING);
      _engineSpeak(responseText);
    }
  }

  // --- UI de voz ----------------------------------------------------

  function _onLiveTranscript(text) { if (text) ILUUI.liveUserMessage(text); }
  function _onVoiceListening(on) { _setMicUI(on ? 'listening' : 'idle'); }
  function _onVoiceModeChange(on) { _setMicUI(on ? 'listening' : 'idle'); document.body.classList.toggle('voice-mode', on); if (on) ILUCore.showListening(); else ILUCore.showIdle(); }
  function _onVoiceError(err) { ILUUI.appendMessage('assistant', 'Error de voz: ' + err, 'VOZ'); ILUCore.set(ILUCore.STATES.ERROR); setTimeout(function () { ILUCore.showIdle(); }, 2500); }
  function _onVoiceUnavailable(reason) {
    if (reason === 'mic_permission') {
      ILUUI.appendMessage('assistant', 'Concede acceso al micrófono para poder conversar conmigo.', 'VOZ'); _setMicUI('idle');
    } else {
      ILUUI.appendMessage('assistant', 'La voz no está disponible en este navegador.', 'VOZ');
      if (_voiceEngine !== 'realtime') { var micBtn = document.getElementById('micButton'); if (micBtn) micBtn.disabled = true; }
    }
  }
  function _onRealtimeListening(on) { _setMicUI(on ? 'listening' : 'idle'); }
  function _onCapturing(on) { _setMicUI(on ? 'live' : 'listening'); if (on) ILUCore.showListening(); }
  function _onSpeaking(on) { _setMicUI(on ? 'speaking' : 'listening'); if (on) ILUCore.set(ILUCore.STATES.RESPONDING); else ILUCore.showListening(); }
  function _onBargeIn() { ILUCore.showListening(); }
  function _setMicUI(state) {
    var micBtn = document.getElementById('micButton');
    if (!micBtn) return;
    micBtn.classList.remove('active', 'speaking', 'live');
    if (state === 'listening' || state === 'active') micBtn.classList.add('active');
    else if (state === 'live') micBtn.classList.add('active', 'live');
    else if (state === 'speaking') micBtn.classList.add('speaking');
  }

  // --- PIN modal promise helper ------------------------------------

  function _requestPin(message) {
    return new Promise(function (resolve) {
      _pinResolve = resolve;
      document.getElementById('pinInput').value = '';
      document.getElementById('pinModalMessage').textContent = message || 'Ingresa tu PIN para confirmar la acción.';
      document.getElementById('pinModalOverlay').classList.add('visible');
      document.getElementById('pinModal').classList.add('visible');
      document.getElementById('pinInput').focus();
    }).then(function (pin) {
      document.getElementById('pinModalOverlay').classList.remove('visible');
      document.getElementById('pinModal').classList.remove('visible');
      _pinResolve = null;
      return pin;
    });
  }

  // Exponer para ILUUI
  window.ILUApp = {
    _requestPin: _requestPin,
    _ownerActor: async function () {
      var sec = await ILUApi.security();
      return (sec && !sec.error && sec.owner) ? sec.owner : 'owner';
    }
  };

  // --- Arranque -----------------------------------------------------

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();