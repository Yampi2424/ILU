/**
 * I.L.U. — Motor de conversación de voz EN TIEMPO REAL
 *
 * Reemplaza la simulación: captura el micrófono REAL con Web Audio API
 * (getUserMedia + AnalyserNode), detecta el habla del usuario (VAD por
 * energía), reconoce con Web Speech, y reproduce la voz REAL de I.L.U.
 * sintetizada por el backend (/tts → edge-tts) a través del MISMO gráfico
 * de audio con un AnalyserNode. La visualización (onda del micrófono,
 * onda de la voz de I.L.U., latido del plasma) se deriva del audio real,
 * nunca se simula.
 *
 * Características reales:
 *   - Escucha continua con turnos naturales (VAD + reconocimiento).
 *   - Barge-in: si hablas mientras I.L.U. responde, la interrumpe al
 *     instante (se detiene la reproducción) y captura tu nuevo turno.
 *   - Presencia como centro: el plasma reacciona a la energía de audio
 *     REAL — del micrófono al escuchar, de la propia voz de I.L.U. al
 *     responder. No hay barras ni ecualizadores.
 *   - Fallback: si el backend TTS no responde, cae al TTS del navegador
 *     (Web Speech) para no quedarse muda.
 *
 * La voz SOLO produce texto y reproduce texto: entra por el MISMO /ask
 * con la MISMA memoria, sesión, seguridad y proveedores. Nunca es un
 * bypass de permisos.
 */

window.ILURealtime = (function () {
  'use strict';

  // ==================================================================
  // Constantes de VAD (ajustables)
  // ==================================================================
  var VAD_START = 0.045;        // RMS para iniciar detección de habla
  var VAD_END = 0.028;          // RMS para considerar silencio
  var VAD_BARGE = 0.030;        // RMS que dispara barge-in durante TTS
  var SPEECH_HANGOVER = 12;     // frames (~600ms) antes de cerrar turno
  var BARGE_FRAMES = 4;         // frames consecutivos de energía para interrumpir

  // ==================================================================
  // Audio graph
  // ==================================================================
  var _AC = null;               // AudioContext
  var _micStream = null;        // MediaStream del micrófono
  var _micSource = null;        // MediaStreamAudioSourceNode
  var _micAnalyser = null;      // AnalyserNode del micrófono
  var _micData = null;          // Uint8Array time-domain del mic
  var _speakAnalyser = null;    // AnalyserNode de la voz de I.L.U.
  var _speakData = null;        // Uint8Array time-domain del TTS
  var _currentSource = null;    // AudioBufferSourceNode en reproducción

  // ==================================================================
  // Estado
  // ==================================================================
  var _supported = false;
  var _active = false;          // sesión de voz armada
  var _speaking = false;        // I.L.U. está reproduciendo audio
  var _processing = false;      // esperando respuesta de /ask
  var _capturing = false;       // el usuario está hablando (VAD)
  var _interrupted = false;     // el turno actual fue interrumpido
  var _rafId = null;            // id del loop de animación

  // ==================================================================
  // Reconocimiento (Web Speech)
  // ==================================================================
  var _SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  var _rec = null;              // reconocedor activo
  var _recActive = false;       // ¿reconocedor arrancado?
  var _recTimer = null;         // temporizador de re-arme
  var _interim = '';            // transcripción parcial actual

  // ==================================================================
  // VAD
  // ==================================================================
  var _vadState = 'idle';       // 'idle' | 'speech'
  var _hangover = 0;            // frames de silencio acumulados
  var _bargeCount = 0;          // frames de energía durante TTS

  // ==================================================================
  // Callbacks hacia la UI (ILUApp)
  // ==================================================================
  var _cb = {};

  function _emit(name, arg) {
    if (_cb[name]) { try { _cb[name](arg); } catch (_) {} }
  }

  // ==================================================================
  // Soporte
  // ==================================================================

  function _supportsRealtime() {
    return !!(
      _SR
      && (window.AudioContext || window.webkitAudioContext)
      && navigator.mediaDevices
      && navigator.mediaDevices.getUserMedia
    );
  }

  function init() {
    _supported = _supportsRealtime();
    return _supported;
  }

  function isAvailable() { return _supported; }
  function isActive() { return _active; }
  function isSpeaking() { return _speaking; }
  function isCapturing() { return _capturing; }

  function setCallbacks(cb) { _cb = cb || {}; }

  // ==================================================================
  // Arranque / parada
  // ==================================================================

  function start() {
    if (!_supported || _active) return false;

    navigator.mediaDevices.getUserMedia({ audio: true })
      .then(_setupGraph)
      .then(function () {
        _active = true;
        _emit('onModeChange', true);
        _goListening('Voz real activa — habla para conversar');
      })
      .catch(function (err) {
        _emit('onUnavailable', 'mic_permission');
        _emit('onError', 'No se pudo acceder al micrófono: ' + (err && err.name));
      });

    return true;
  }

  function toggle() {
    if (_active) { stop(); return false; }
    return start();
  }

  function stop() {
    _active = false;
    _speaking = false;
    _processing = false;
    _capturing = false;
    _interrupted = false;
    _vadState = 'idle';
    _hangover = 0;
    _bargeCount = 0;

    _stopRecognition();
    _stopPlayback();

    if (_rafId) { cancelAnimationFrame(_rafId); _rafId = null; }

    if (_micSource && _micSource.disconnect) _micSource.disconnect();
    if (_micStream) {
      _micStream.getTracks().forEach(function (t) { try { t.stop(); } catch (_) {} });
      _micStream = null;
    }

    _setPlasmaEnergy(0);
    _emit('onModeChange', false);
  }

  // ==================================================================
  // Setup del gráfico de audio
  // ==================================================================

  function _setupGraph(stream) {
    _AC = new (window.AudioContext || window.webkitAudioContext)();
    // La creación ocurre tras un gesto de usuario (clic en el micrófono),
    // pero algunos navegadores suspenden el contexto: lo reanudamos.
    if (_AC.state === 'suspended' && _AC.resume) {
      try { _AC.resume(); } catch (_) {}
    }
    _micStream = stream;

    _micSource = _AC.createMediaStreamSource(stream);
    _micAnalyser = _AC.createAnalyser();
    _micAnalyser.fftSize = 2048;
    _micAnalyser.smoothingTimeConstant = 0.55;
    _micSource.connect(_micAnalyser);
    _micData = new Uint8Array(_micAnalyser.fftSize);

    _speakAnalyser = _AC.createAnalyser();
    _speakAnalyser.fftSize = 2048;
    _speakAnalyser.smoothingTimeConstant = 0.6;
    _speakData = new Uint8Array(_speakAnalyser.fftSize);

    // No se conecta el micrófono al destino: solo se analiza (VAD + voz).

    _rafId = requestAnimationFrame(_loop);
  }

  // ==================================================================
  // Loop principal: VAD + visualización
  // ==================================================================

  function _loop() {
    if (!_active) return;
    _rafId = requestAnimationFrame(_loop);

    var micLevel = _readLevel(_micAnalyser, _micData);
    var speakLevel = _readLevel(_speakAnalyser, _speakData);

    // El plasma reacciona a la voz REAL: del usuario al escuchar, de
    // I.L.U. al responder (su propia voz). No hay barras ni ondas
    // separadas: la presencia ES la visualización.
    if (_speaking) _setPlasmaEnergy(speakLevel);
    else _setPlasmaEnergy(micLevel);

    _runVad(micLevel);
  }

  /**
   * Nivel RMS [0..1] de un AnalyserNode (time-domain).
   */
  function _readLevel(analyser, data) {
    if (!analyser) return 0;
    analyser.getByteTimeDomainData(data);
    var sum = 0;
    for (var i = 0; i < data.length; i++) {
      var v = (data[i] - 128) / 128;
      sum += v * v;
    }
    return Math.sqrt(sum / data.length);
  }

  // ==================================================================
  // VAD: turnos naturales + barge-in
  // ==================================================================

  function _runVad(micLevel) {
    if (_processing || _speaking) {
      // Mientras I.L.U. responde, el micrófono vigila interrupciones
      // (barge-in): si el usuario habla, se corta la reproducción.
      if (_speaking && !_interrupted) {
        if (micLevel > VAD_BARGE) _bargeCount++;
        else if (_bargeCount > 0) _bargeCount--;

        if (_bargeCount >= BARGE_FRAMES) _bargeIn();
      }
      return;
    }

    // Turno del usuario: detectar inicio/fin de habla por energía.
    if (_vadState === 'idle') {
      if (micLevel > VAD_START) {
        _vadState = 'speech';
        _capturing = true;
        _hangover = 0;
        _emit('onCapturing', true);
      }
    } else {
      if (micLevel > VAD_END) {
        _hangover = 0;
      } else {
        _hangover++;
        if (_hangover >= SPEECH_HANGOVER) {
          _vadState = 'idle';
          _capturing = false;
          _emit('onCapturing', false);
        }
      }
    }
  }

  // ==================================================================
  // Barge-in: el usuario interrumpe a I.L.U.
  // ==================================================================

  function _bargeIn() {
    if (!_speaking || _interrupted) return;
    _interrupted = true;
    _bargeCount = 0;

    _stopPlayback();
    _speaking = false;

    _emit('onBargeIn');
    _emit('onSpeaking', false);
    _goListening('Interrumpido — te escucho');
  }

  // ==================================================================
  // Estados de escucha
  // ==================================================================

  function _goListening(status) {
    _emit('onListening', true);
    if (status) _emit('onStatus', status);
    _armRecognition();
  }

  function _startRecognition() {
    if (!_active || _speaking || _processing || _recActive || !_SR) return;

    _recActive = true;
    _interim = '';

    var rec = new _SR();
    rec.lang = 'es-AR';
    rec.continuous = false;   // un turno por activación → turno natural
    rec.interimResults = true;
    rec.maxAlternatives = 1;

    rec.onresult = function (e) {
      var finalText = '';
      var interim = '';
      for (var i = 0; i < e.results.length; i++) {
        var res = e.results[i];
        if (res.isFinal) finalText += res[0].transcript;
        else interim += res[0].transcript;
      }
      if (interim) { _interim = interim; _emit('onInterim', interim); }
      if (finalText && finalText.trim()) {
        _handleUtterance(finalText.trim());
      }
    };

    rec.onerror = function () {
      _recActive = false;
      _rec = null;
      _armRecognition();
    };

    rec.onend = function () {
      _recActive = false;
      _rec = null;
      // Si aún hay texto provisional sin confirmar, no lo perdemos:
      // un turno largo puede superar la activación única.
      if (_interim && !_processing && !_speaking) {
        _handleUtterance(_interim.trim());
        return;
      }
      _armRecognition();
    };

    try { rec.start(); }
    catch (_) { _recActive = false; _armRecognition(); }

    _rec = rec;
  }

  function _armRecognition() {
    if (_recTimer) { clearTimeout(_recTimer); _recTimer = null; }
    _recTimer = setTimeout(_startRecognition, 350);
  }

  function _stopRecognition() {
    if (_recTimer) { clearTimeout(_recTimer); _recTimer = null; }
    if (_recActive && _rec) { try { _rec.abort(); } catch (_) {} }
    _recActive = false;
    _rec = null;
  }

  // ==================================================================
  // Turno: texto reconocido → /ask
  // ==================================================================

  function _handleUtterance(text) {
    if (!_active || _processing || _speaking) return;
    if (!text) return;

    _processing = true;
    _capturing = false;
    _vadState = 'idle';
    _hangover = 0;

    _stopRecognition();
    _emit('onListening', false);
    _emit('onUtterance', text);
  }

  // ==================================================================
  // Reproducción de la voz de I.L.U. (TTS real por Web Audio)
  // ==================================================================

  function speakResponse(text) {
    if (!_active) return;
    if (!text) { _finishProcessing(); return; }

    // La respuesta ya llegó: ya no estamos "procesando"; pasamos a
    // hablar. Esto permite el barge-in (re-armar el reconocimiento).
    _processing = false;
    _speaking = true;
    _interrupted = false;
    _bargeCount = 0;

    _stopRecognition();   // no transcribir el eco de I.L.U.
    _emit('onSpeaking', true);

    fetch('/tts?text=' + encodeURIComponent(text), { cache: 'no-store' })
      .then(function (res) {
        if (!res.ok) throw new Error('tts_' + res.status);
        return res.arrayBuffer();
      })
      .then(function (buf) {
        if (!_active || !_speaking) return;   // ya fue interrumpido
        return _AC.decodeAudioData(buf);
      })
      .then(function (audioBuffer) {
        if (!_active || !_speaking) return;
        _playBuffer(audioBuffer);
      })
      .catch(function () {
        // Backend TTS no disponible: fallo al TTS nativo del navegador.
        _speakFallback(text);
      });
  }

  function _playBuffer(audioBuffer) {
    var src = _AC.createBufferSource();
    src.buffer = audioBuffer;
    src.connect(_speakAnalyser);
    _speakAnalyser.connect(_AC.destination);
    _currentSource = src;
    src.onended = function () { _currentSource = null; _onSpeakEnded(); };
    try { src.start(0); } catch (_) { _currentSource = null; _onSpeakEnded(); }
  }

  function _onSpeakEnded() {
    if (!_active) return;
    _speaking = false;
    _currentSource = null;
    _interrupted = false;
    _bargeCount = 0;
    _setPlasmaEnergy(0);
    _emit('onSpeaking', false);
    _finishProcessing();
  }

  function _stopPlayback() {
    if (_currentSource) {
      try { _currentSource.onended = null; _currentSource.stop(); } catch (_) {}
      _currentSource = null;
    }
  }

  /**
   * Fallback: TTS nativo del navegador (Web Speech) si el backend
   * no respondió. Sin visualización analizable, pero I.L.U. no se
   * queda muda. Se usa speechSynthesis si existe.
   */
  function _speakFallback(text) {
    if (!window.speechSynthesis) { _onSpeakEnded(); return; }

    var utter = new SpeechSynthesisUtterance(text);
    utter.lang = 'es-AR';
    utter.rate = 1.0;

    var voices = window.speechSynthesis.getVoices() || [];
    var es = voices.filter(function (v) { return v.lang && v.lang.indexOf('es') === 0; });
    if (es.length) utter.voice = es[0];

    utter.onend = _onSpeakEnded;
    utter.onerror = _onSpeakEnded;
    window.speechSynthesis.speak(utter);
  }

  // ==================================================================
  // Fin del turno
  // ==================================================================

  function _finishProcessing() {
    _processing = false;
    _emit('onListening', true);
    _armRecognition();
  }

  function cancelTurn() {
    // Usado por texto escrito para cancelar la petición en curso.
    _processing = false;
    _stopPlayback();
    _speaking = false;
    _emit('onListening', true);
    _armRecognition();
  }

  /**
   * Cierra el turno sin hablar (error / autorización / sin respuesta):
   * libera la reproducción, y vuelve a escuchar. Evita que el motor
   * quede atascado en "processing" cuando la respuesta no es texto.
   */
  function finishTurn() {
    _processing = false;
    _speaking = false;
    _interrupted = false;
    _bargeCount = 0;
    _stopPlayback();
    _setPlasmaEnergy(0);
    _emit('onSpeaking', false);
    _emit('onListening', true);
    _armRecognition();
  }

  // ==================================================================
  // Visualización
  // ==================================================================

  function _setPlasmaEnergy(level) {
    if (window.ILUPlasma && window.ILUPlasma.setEnergy) {
      window.ILUPlasma.setEnergy(level);
    }
  }

  // ==================================================================
  // API pública
  // ==================================================================

  return {
    init: init,
    isAvailable: isAvailable,
    isActive: isActive,
    isSpeaking: isSpeaking,
    isCapturing: isCapturing,
    setCallbacks: setCallbacks,
    start: start,
    toggle: toggle,
    stop: stop,
    speakResponse: speakResponse,
    cancelTurn: cancelTurn,
    finishTurn: finishTurn
  };
})();
