/**
 * I.L.U. — Componente visual de la Presencia
 *
 * Gestiona el estado visual de I.L.U. delegando al motor de plasma
 * (ILUPlasma) que renderiza en Canvas.
 *
 * Estados:
 *   idle, listening, thinking, working, responding,
 *   learning, authorization, error, emergency
 *
 * Este módulo NO toma decisiones de autoridad;
 * solo refleja lo que el backend reporta.
 */

window.ILUCore = (function () {
  'use strict';

  const STATES = {
    IDLE:           'idle',
    LISTENING:      'listening',
    THINKING:       'thinking',
    WORKING:        'working',
    RESPONDING:     'responding',
    LEARNING:       'learning',
    AUTHORIZATION:  'authorization',
    ERROR:          'error',
    EMERGENCY:      'emergency'
  };

  const STATE_LABELS = {
    idle:           'I.L.U. está presente',
    listening:      'Te escucho',
    thinking:       'Pensando…',
    working:        'Trabajando…',
    responding:     'Te hablo',
    learning:       'Aprendiendo…',
    authorization:  'Esperando autorización…',
    error:          'Error',
    emergency:      'Emergencia activa'
  };

  let _current = STATES.IDLE;
  let _labelEl = null;
  let _initialized = false;
  let _timers = [];

  // Gestión de transiciones temporales: cada nuevo estado programado se
  // cancela si llega otra respuesta. Evita que un setTimeout viejo
  // "encienda" un estado obsoleto sobre uno más reciente.
  function _clearTimers() {
    _timers.forEach(function (t) { clearTimeout(t); });
    _timers = [];
  }

  function _schedule(fn, ms) {
    var id = setTimeout(function () {
      _timers = _timers.filter(function (t) { return t !== id; });
      fn();
    }, ms);
    _timers.push(id);
  }

  function _init() {
    if (_initialized) return;
    _labelEl = document.getElementById('stateLabel');

    // Inicializar motor de plasma
    if (window.ILUPlasma && window.ILUPlasma.init()) {
      window.ILUPlasma.start();
    }

    _initialized = true;
  }

  function set(state) {
    if (!_initialized) _init();

    var validState = STATES[state.toUpperCase()];
    if (!validState) return;

    _current = validState;

    // Delegar al motor de plasma
    if (window.ILUPlasma) {
      window.ILUPlasma.setState(validState);
    }

    // Actualizar label
    if (_labelEl) {
      _labelEl.textContent = STATE_LABELS[validState] || '';
      _labelEl.setAttribute('data-state', validState);
    }
  }

  function get() {
    return _current;
  }

  function isIdle() {
    return _current === STATES.IDLE;
  }

  /**
   * Aplica el estado visual basado en la respuesta del backend.
   *
   * Mapeo:
   *   success=false + authorization=ask  → authorization
   *   success=false + otro               → error
   *   success=true + tool=*              → working (efímero)
   *   success=true (texto)               → responding (efímero)
   */
  function applyFromResponse(result) {
    // Nueva respuesta: se cancelan las transiciones pendientes de la
    // anterior para que un estado obsoleto no pise al actual.
    _clearTimers();

    if (!result) {
      set(STATES.IDLE);
      return;
    }

    if (result.authorization === 'ask') {
      set(STATES.AUTHORIZATION);
      return;
    }

    if (!result.success) {
      set(STATES.ERROR);
      _schedule(function () { set(STATES.IDLE); }, 4000);
      return;
    }

    if (result.tool) {
      set(STATES.WORKING);
      _schedule(function () { set(STATES.RESPONDING); }, 300);
      _schedule(function () { set(STATES.IDLE); }, 2500);
      return;
    }

    set(STATES.RESPONDING);
    _schedule(function () { set(STATES.IDLE); }, 2500);
  }

  /**
   * Estado transitorio: el usuario está escribiendo
   * (la interfaz lo interpreta; no viene del backend).
   */
  function showListening() {
    set(STATES.LISTENING);
  }

  function showThinking() {
    set(STATES.THINKING);
  }

  function showIdle() {
    set(STATES.IDLE);
  }

  return {
    STATES: STATES,
    STATE_LABELS: STATE_LABELS,
    init: _init,
    set: set,
    get: get,
    isIdle: isIdle,
    applyFromResponse: applyFromResponse,
    showListening: showListening,
    showThinking: showThinking,
    showIdle: showIdle
  };
})();