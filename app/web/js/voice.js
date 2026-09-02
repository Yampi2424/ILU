/**
 * I.L.U. — Capa de voz (conversación por voz)
 *
 * Orquesta: micrófono → Speech-to-Text → texto → (ILUApp.sendText)
 *   → /ask → ILUCore → respuesta → Text-to-Speech → voz de I.L.U.
 *
 * PRINCIPIO: la voz SOLO produce texto y reproduce texto. Entra por el
 * MISMO /ask que el texto escrito, con el mismo session_id, la misma
 * memoria, los mismos proveedores y las mismas reglas de seguridad.
 * La voz NO es identidad, NO es un segundo cerebro y NO es un bypass
 * de permisos: una orden hablada tiene exactamente el mismo peso que
 * la misma orden escrita (SecurityGate / Authority intactos).
 *
 * Providers intercambiables (patrón strategy):
 *   - SpeechRecognizer:  webSpeech (hoy) → whisper (local, futuro)
 *   - SpeechSynthesizer: webSpeech (hoy) → piper (local, futuro)
 * La interfaz no cambia al intercambiar el motor.
 *
 * Anti-autoescucha: el reconocimiento NUNCA se activa mientras I.L.U.
 * está hablando (TTS) o procesando; solo se re-arma en IDLE tras una
 * ventana de guarda.
 */

window.ILUVoice = (function () {
  'use strict';

  // ==================================================================
  // Interfaces de proveedor (strategy — intercambiables)
  // ==================================================================

  /**
   * Reconocimiento de voz (STT) vía Web Speech API del navegador.
   * Devuelve null si el navegador no lo soporta.
   */
  function createWebSpeechRecognizer() {
    var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) return null;

    return {
      isAvailable: function () { return true; },
      start: function (handlers) {
        var rec = new SR();
        rec.lang = 'es-AR';
        rec.continuous = false;
        rec.interimResults = true;
        rec.maxAlternatives = 1;

        rec.onresult = function (e) {
          var interim = '';
          var finalText = '';
          for (var i = 0; i < e.results.length; i++) {
            var res = e.results[i];
            if (res.isFinal) finalText += res[0].transcript;
            else interim += res[0].transcript;
          }
          if (interim && handlers.onInterim) handlers.onInterim(interim);
          if (finalText && handlers.onResult) handlers.onResult(finalText.trim());
        };
        rec.onerror = function (e) {
          if (handlers.onError) handlers.onError(e.error || 'error');
        };
        rec.onend = function () {
          if (handlers.onEnd) handlers.onEnd();
        };

        try { rec.start(); }
        catch (e) { if (handlers.onError) handlers.onError('not-allowed'); }

        return rec;
      }
    };
  }

  /**
   * Síntesis de voz (TTS) vía Web Speech API (voces del sistema).
   * Devuelve null si el navegador no lo soporta.
   */
  function createWebSpeechSynthesizer() {
    if (!window.speechSynthesis) return null;

    return {
      isAvailable: function () { return true; },
      getVoices: function () {
        return window.speechSynthesis.getVoices() || [];
      },
      speak: function (text, handlers) {
        var utter = new SpeechSynthesisUtterance(text);
        utter.lang = 'es-AR';
        utter.rate = 1.0;
        utter.pitch = 1.0;

        var voices = this.getVoices();
        var es = voices.filter(function (v) {
          return v.lang && v.lang.indexOf('es') === 0;
        });
        if (es.length) utter.voice = es[0];

        if (handlers.onStart) utter.onstart = handlers.onStart;
        if (handlers.onEnd) utter.onend = handlers.onEnd;
        if (handlers.onError) utter.onerror = handlers.onError;

        window.speechSynthesis.speak(utter);
      },
      cancel: function () {
        window.speechSynthesis.cancel();
      }
    };
  }

  // ==================================================================
  // Estado interno
  // ==================================================================

  var _recognizer = null;      // provider STT activo
  var _synthesizer = null;     // provider TTS activo
  var _supports = false;       // ¿el navegador soporta voz?
  var _active = false;         // sesión de voz activa (micrófono armado)
  var _listening = false;      // capturando micrófono
  var _speaking = false;       // I.L.U. está hablando (TTS)
  var _busy = false;           // procesando una petición en /ask
  var _continuous = true;      // modo conversación continua
  var _onTranscript = null;    // callback(texto_final) → envía a /ask
  var _activeRec = null;       // instancia actual del reconocedor
  var _guardTimer = null;      // temporizador anti-eco

  // Callbacks hacia la UI
  var _onInterim = null;
  var _onTranscribed = null;
  var _onListening = null;
  var _onError = null;
  var _onUnavailable = null;
  var _onModeChange = null;

  // ==================================================================
  // Utilidades
  // ==================================================================

  function _setState(state) {
    if (window.ILUCore) window.ILUCore.set(state);
  }

  function _clearGuard() {
    if (_guardTimer) { clearTimeout(_guardTimer); _guardTimer = null; }
  }

  // ==================================================================
  // API pública
  // ==================================================================

  function init() {
    _recognizer = createWebSpeechRecognizer();
    _synthesizer = createWebSpeechSynthesizer();
    _supports = !!(_recognizer && _synthesizer);
    return _supports;
  }

  function isAvailable() { return _supports; }
  function isActive() { return _active; }
  function isListening() { return _listening; }
  function isSpeaking() { return _speaking; }

  function configure(opts) {
    if (opts.onTranscript) _onTranscript = opts.onTranscript;
    if (typeof opts.continuous === 'boolean') _continuous = opts.continuous;
  }

  function setCallbacks(cb) {
    if (cb.onInterim) _onInterim = cb.onInterim;
    if (cb.onTranscribed) _onTranscribed = cb.onTranscribed;
    if (cb.onListening) _onListening = cb.onListening;
    if (cb.onError) _onError = cb.onError;
    if (cb.onUnavailable) _onUnavailable = cb.onUnavailable;
    if (cb.onModeChange) _onModeChange = cb.onModeChange;
  }

  /**
   * Arma/desarma la sesión de voz.
   * Al armar: empieza a escuchar (IDLE → LISTENING).
   * Al desarmar: detiene todo y vuelve a IDLE.
   */
  function toggle() {
    if (!_supports) { _notifyUnavailable(); return false; }
    if (_active) { stop(); return false; }
    start();
    return true;
  }

  function start() {
    if (!_supports || _active) return;
    _active = true;
    _notifyModeChange(true);
    _beginListening();
  }

  function stop() {
    _active = false;
    _listening = false;
    _busy = false;
    _speaking = false;
    _clearGuard();
    if (_activeRec && _activeRec.abort) { try { _activeRec.abort(); } catch (_) {} }
    if (_synthesizer) _synthesizer.cancel();
    _activeRec = null;
    _setState('idle');
    _notifyModeChange(false);
  }

  /**
   * Cancela la petición en curso sin desactivar la voz
   * (p. ej. si el usuario escribió mientras el micrófono escuchaba).
   */
  function cancelTurn() {
    _busy = false;
    _listening = false;
    _clearGuard();
    _setState('idle');
    if (_active && _continuous) {
      _guardTimer = setTimeout(_beginListening, 500);
    }
  }

  // ==================================================================
  // Escucha (STT)
  // ==================================================================

  function _beginListening() {
    // Anti-autoescucha: nunca escuchar mientras I.L.U. habla o procesa.
    if (!_active || _speaking || _busy) { _scheduleListen(); return; }

    _listening = true;
    _setState('listening');
    _notifyListening(true);

    _activeRec = _recognizer.start({
      onInterim: _notifyInterim,
      onResult: _handleTranscript,
      onError: _handleError,
      onEnd: function () { /* la escucha continua se re-arma en _finishTurn */ }
    });
  }

  function _handleTranscript(text) {
    if (!_active) return;
    _listening = false;
    _notifyListening(false);

    if (!text) { _retryListen(); return; }

    _notifyTranscribed(text);
    _busy = true;
    _setState('thinking');

    if (_onTranscript) _onTranscript(text);
    else _finishTurn();
  }

  function _retryListen() {
    _listening = false;
    _clearGuard();
    _guardTimer = setTimeout(_beginListening, 300);
  }

  function _scheduleListen() {
    _clearGuard();
    _guardTimer = setTimeout(_beginListening, 400);
  }

  function _handleError(err) {
    _listening = false;
    _busy = false;
    _setState('error');
    _notifyError(err);
    setTimeout(function () {
      if (_active) { _setState('idle'); _scheduleListen(); }
    }, 2500);
  }

  // ==================================================================
  // Habla (TTS)
  // ==================================================================

  /**
   * Reproduce la respuesta de I.L.U. y gestiona responding → idle.
   * Al terminar, re-arma la escucha (si es continua y está activa).
   */
  function speakResponse(text) {
    if (!_active || !_synthesizer) { _finishTurn(); return; }
    if (!text) { _finishTurn(); return; }

    _speaking = true;
    _busy = false;
    _setState('responding');

    _synthesizer.speak(text, {
      onStart: function () { _setState('responding'); },
      onEnd: function () { _finishTurn(); },
      onError: function () { _finishTurn(); }
    });
  }

  function speakError() {
    if (!_active || !_synthesizer) return;
    _speaking = true;
    _setState('error');
    _synthesizer.speak('Lo siento, ocurrió un error.', {
      onEnd: function () { _finishTurn(); },
      onError: function () { _finishTurn(); }
    });
  }

  function speakAuthorization() {
    if (!_active || !_synthesizer) return;
    _speaking = true;
    _setState('authorization');
    _synthesizer.speak(
      'Necesito tu autorización para continuar. Revisa la pestaña de permisos.',
      {
        onEnd: function () { _finishTurn(); },
        onError: function () { _finishTurn(); }
      }
    );
  }

  function _finishTurn() {
    _speaking = false;
    _busy = false;
    _listening = false;
    _clearGuard();
    _setState('idle');

    if (_active && _continuous) {
      // Ventana de guarda antes de volver a escuchar (evita captar el eco).
      _guardTimer = setTimeout(_beginListening, 500);
    } else {
      _notifyModeChange(false);
    }
  }

  // ==================================================================
  // Notificaciones hacia la UI
  // ==================================================================

  function _notifyInterim(text) { if (_onInterim) _onInterim(text); }
  function _notifyTranscribed(text) { if (_onTranscribed) _onTranscribed(text); }
  function _notifyListening(on) { if (_onListening) _onListening(on); }
  function _notifyError(err) { if (_onError) _onError(err); }
  function _notifyUnavailable() { if (_onUnavailable) _onUnavailable(); }
  function _notifyModeChange(on) { if (_onModeChange) _onModeChange(on); }

  return {
    init: init,
    isAvailable: isAvailable,
    isActive: isActive,
    isListening: isListening,
    isSpeaking: isSpeaking,
    configure: configure,
    setCallbacks: setCallbacks,
    toggle: toggle,
    start: start,
    stop: stop,
    cancelTurn: cancelTurn,
    speakResponse: speakResponse,
    speakError: speakError,
    speakAuthorization: speakAuthorization
  };
})();
