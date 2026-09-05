/**
 * I.L.U. — Cliente del API HTTP
 *
 * Módulo de comunicación con el backend de I.L.U.
 * SOLO lectura de datos y envío de órdenes.
 * NUNCA toma decisiones de permisos ni ejecuta autoridad.
 *
 * Cada función devuelve una promesa con la respuesta JSON.
 */

window.ILUApi = (function () {
  'use strict';

  const BASE = '';
  const TOKEN_KEY = 'ilu_device_token';
  const PIN_KEY = 'ilu_owner_pin';

  /**
   * Cabeceras de autorización.
   *
   * - El token de dispositivo (X-ILU-Token) protege la MÁQUINA: el owner
   *   lo configura con ILUApi.setToken(); se guarda en localStorage.
   * - El secreto del owner (X-ILU-Pin) es el MISMO PIN de la concesión
   *   por voz/texto: se guarda en sessionStorage (NO persiste entre
   *   sesiones del navegador) y solo se envía en las rutas
   *   administrativas.
   *
   * Las rutas administrativas (grants, autonomía, resolución de
   * solicitudes, borrado de conversaciones) aceptan cualquiera de las
   * dos credenciales.
   */
  function _authHeaders(extra, includePin) {
    let headers = extra ? Object.assign({}, extra) : {};
    let token = null;
    try { token = window.localStorage.getItem(TOKEN_KEY); } catch (_) {}
    if (token) headers['X-ILU-Token'] = token;
    if (includePin) {
      let pin = null;
      try { pin = window.sessionStorage.getItem(PIN_KEY); } catch (_) {}
      if (pin) headers['X-ILU-Pin'] = pin;
    }
    return headers;
  }

  async function _get(path) {
    try {
      const response = await fetch(BASE + path, {
        method: 'GET',
        headers: _authHeaders({ 'Accept': 'application/json' })
      });
      return await response.json();
    } catch (error) {
      console.error(`[I.L.U. API] GET ${path}:`, error);
      return { error: 'network_error', detail: String(error) };
    }
  }

  async function _post(path, body, includePin) {
    try {
      const response = await fetch(BASE + path, {
        method: 'POST',
        headers: _authHeaders({
          'Content-Type': 'application/json',
          'Accept': 'application/json'
        }, includePin),
        body: JSON.stringify(body)
      });
      return await response.json();
    } catch (error) {
      console.error(`[I.L.U. API] POST ${path}:`, error);
      return { error: 'network_error', detail: String(error) };
    }
  }

  async function _put(path, body) {
    try {
      const response = await fetch(BASE + path, {
        method: 'PUT',
        headers: _authHeaders({
          'Content-Type': 'application/json',
          'Accept': 'application/json'
        }),
        body: JSON.stringify(body)
      });
      return await response.json();
    } catch (error) {
      console.error(`[I.L.U. API] PUT ${path}:`, error);
      return { error: 'network_error', detail: String(error) };
    }
  }

  async function _delete(path, includePin) {
    try {
      const response = await fetch(BASE + path, {
        method: 'DELETE',
        headers: _authHeaders({ 'Accept': 'application/json' }, includePin)
      });
      return await response.json();
    } catch (error) {
      console.error(`[I.L.U. API] DELETE ${path}:`, error);
      return { error: 'network_error', detail: String(error) };
    }
  }

  return {

    // --- Autorización de dispositivo ---
    setToken: function (token) {
      try {
        if (token) window.localStorage.setItem(TOKEN_KEY, token);
        else window.localStorage.removeItem(TOKEN_KEY);
      } catch (_) {}
    },
    hasToken: function () {
      try { return !!window.localStorage.getItem(TOKEN_KEY); } catch (_) { return false; }
    },

    // --- Secreto del owner (MISMO PIN de la concesión por voz/texto) ---
    setPin: function (pin) {
      try {
        if (pin) window.sessionStorage.setItem(PIN_KEY, pin);
        else window.sessionStorage.removeItem(PIN_KEY);
      } catch (_) {}
    },
    getPin: function () {
      try { return window.sessionStorage.getItem(PIN_KEY) || ''; } catch (_) { return ''; }
    },
    hasPin: function () {
      try { return !!window.sessionStorage.getItem(PIN_KEY); } catch (_) { return false; }
    },
    // --- Estado ---
    healthz: function () { return _get('/healthz'); },
    about: function () { return _get('/about'); },
    state: function () { return _get('/state'); },
    notifications: function (limit) {
      let path = '/notifications';
      if (limit) path += '?limit=' + encodeURIComponent(String(limit));
      return _get(path);
    },

    // --- Conversación ---
    ask: function (message, sessionId) {
      const body = { message: message };
      if (sessionId) body.session_id = sessionId;
      return _post('/ask', body);
    },

    conversations: function (sessionId, limit) {
      let path = '/conversations/' + encodeURIComponent(sessionId || 'default');
      if (limit) path += '?limit=' + encodeURIComponent(String(limit));
      return _get(path);
    },

    resetConversation: function (sessionId) {
      return _delete(
        '/conversations/' + encodeURIComponent(sessionId || 'default'),
        true
      );
    },

    // --- Tareas ---
    tasks: function (state) {
      let path = '/tasks';
      if (state) path += '?state=' + encodeURIComponent(state);
      return _get(path);
    },

    taskDetail: function (taskId) {
      return _get('/tasks/' + encodeURIComponent(taskId));
    },

    createTask: function (title, description) {
      return _post('/tasks', { title: title, description: description || '' });
    },

    updateTaskState: function (taskId, state) {
      return _put('/tasks/' + encodeURIComponent(taskId) + '/state', { state: state });
    },

    updateTaskProgress: function (taskId, progress) {
      return _put('/tasks/' + encodeURIComponent(taskId) + '/progress', { progress: progress });
    },

    // --- JARVIS Evolution: objetivos, aprendizaje, proactividad,
    //     percepción e integración con dispositivos ---
    goals: function () { return _get('/goals'); },
    goalDetail: function (goalId) {
      return _get('/goals/' + encodeURIComponent(goalId));
    },
    profile: function () { return _get('/profile'); },
    proactivity: function () { return _get('/proactivity'); },
    perception: function () { return _get('/perception'); },
    integrations: function () { return _get('/integrations'); },

    // --- Seguridad / Permisos ---
    security: function () { return _get('/security'); },
    grants: function (params) {
      let path = '/grants';
      if (params && params.capability) path += '?capability=' + encodeURIComponent(params.capability);
      if (params && params.status) path += (path.includes('?') ? '&' : '?') + 'status=' + encodeURIComponent(params.status);
      return _get(path);
    },
    policy: function () { return _get('/policy'); },

    authorizationRequests: function () {
      return _get('/authorization-requests');
    },

    resolveAuthRequest: function (requestId, actor, decision, reason, opts) {
      var body = { actor: actor, decision: decision, reason: reason || '' };
      if (opts && opts.remember) body.remember = true;
      if (opts && opts.indefinite) body.indefinite = true;
      return _post(
        '/authorization-requests/' + encodeURIComponent(requestId),
        body,
        true
      );
    },

    grantPermission: function (actor, capability, reason) {
      return _post('/grants', {
        actor: actor,
        capability: capability,
        reason: reason || ''
      }, true);
    },

    changeAutonomy: function (actor, level) {
      return _post('/autonomy', { actor: actor, level: level }, true);
    }
  };
})();
