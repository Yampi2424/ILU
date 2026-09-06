/**
 * I.L.U. — Cliente del API HTTP
 *
 * Módulo de comunicación con el backend de I.L.U.
 * SOLO lectura de datos y envío de órdenes.
 * NUNCA toma decisiones de permisos ni ejecuta autoridad.
 *
 * Cada función devuelve una promesa con la respuesta JSON.
 *
 * Cabeceras:
 *  - X-ILU-Token: token de dispositivo (localStorage, protege la máquina).
 *  - X-ILU-Pin:   secreto del owner (sessionStorage, MISMO PIN de la
 *                 concesión por voz/texto). Solo se envía en rutas
 *                 administrativas; jamás viaja en logs/audit/commits.
 */

window.ILUApi = (function () {
  'use strict';

  const BASE = '';
  const TOKEN_KEY = 'ilu_device_token';
  const PIN_KEY = 'ilu_owner_pin';

  /**
   * Cabeceras de autorización.
   *
   * Las rutas administrativas (grants, autonomía, resolución de
   * solicitudes, skills, scheduler, agentes, research, memoria,
   * borrado de conversaciones) aceptan cualquiera de las dos
   * credenciales (token de dispositivo o PIN del owner).
   */
  function _authHeaders(extra, includePin) {
    var headers = extra ? Object.assign({}, extra) : {};
    var token = null;
    try { token = window.localStorage.getItem(TOKEN_KEY); } catch (_) {}
    if (token) headers['X-ILU-Token'] = token;
    if (includePin) {
      var pin = null;
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
      console.error('[I.L.U. API] GET ' + path + ':', error);
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
      console.error('[I.L.U. API] POST ' + path + ':', error);
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
      console.error('[I.L.U. API] PUT ' + path + ':', error);
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
      console.error('[I.L.U. API] DELETE ' + path + ':', error);
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
      var path = '/notifications';
      if (limit) path += '?limit=' + encodeURIComponent(String(limit));
      return _get(path);
    },

    // --- Conversación ---
    ask: function (message, sessionId) {
      var body = { message: message };
      if (sessionId) body.session_id = sessionId;
      return _post('/ask', body);
    },

    askStream: function (message, sessionId, handlers) {
      var body = { message: message };
      if (sessionId) body.session_id = sessionId;

      return fetch(BASE + '/ask/stream', {
        method: 'POST',
        headers: _authHeaders({
          'Content-Type': 'application/json',
          'Accept': 'text/event-stream'
        }),
        body: JSON.stringify(body)
      }).then(function (response) {
        if (!response.ok) {
          return response.json().then(function (err) {
            if (handlers && handlers.onError) handlers.onError(err);
            throw new Error(err.error || 'stream_failed');
          });
        }

        var reader = response.body.getReader();
        var decoder = new TextDecoder();
        var buffer = '';

        function read() {
          return reader.read().then(function (_ref) {
            var done = _ref.done;
            var value = _ref.value;

            if (done) {
              if (handlers && handlers.onDone) handlers.onDone();
              return;
            }

            buffer += decoder.decode(value, { stream: true });
            var lines = buffer.split('\n\n');
            buffer = lines.pop() || '';

            lines.forEach(function (line) {
              if (line.indexOf('data: ') !== 0) return;
              try {
                var event = JSON.parse(line.slice(6));
                if (handlers && handlers.onEvent) handlers.onEvent(event);
                switch (event.event) {
                  case 'status':
                    if (handlers && handlers.onStatus) handlers.onStatus(event.state);
                    break;
                  case 'token':
                    if (handlers && handlers.onToken) handlers.onToken(event.text);
                    break;
                  case 'action':
                    if (handlers && handlers.onAction) handlers.onAction(event);
                    break;
                  case 'action_result':
                    if (handlers && handlers.onActionResult) handlers.onActionResult(event);
                    break;
                  case 'final':
                    if (handlers && handlers.onFinal) handlers.onFinal(event.result);
                    break;
                  case 'error':
                    if (handlers && handlers.onError) handlers.onError(event);
                    break;
                }
              } catch (e) {
                // ignorar errores de parseo en chunks parciales
              }
            });

            return read();
          });
        }

        return read();
      }).catch(function () {
        // Fallback a ask() síncrono si el streaming no está disponible.
        if (handlers && handlers.onError) {
          handlers.onError({ error: 'stream_unavailable', fallback: true });
        }
        return _post('/ask', body).then(function (result) {
          if (handlers && handlers.onFinal) handlers.onFinal(result);
          return result;
        });
      });
    },

    conversations: function (sessionId, limit) {
      var path = '/conversations/' + encodeURIComponent(sessionId || 'default');
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
      var path = '/tasks';
      if (state) path += '?state=' + encodeURIComponent(state);
      return _get(path);
    },

    taskDetail: function (taskId) {
      return _get('/tasks/' + encodeURIComponent(taskId));
    },

    createTask: function (title, description, priority) {
      var body = { title: title, description: description || '' };
      if (priority) body.priority = priority;
      return _post('/tasks', body);
    },

    updateTaskState: function (taskId, state) {
      return _put('/tasks/' + encodeURIComponent(taskId) + '/state', { state: state });
    },

    updateTaskProgress: function (taskId, progress) {
      return _put('/tasks/' + encodeURIComponent(taskId) + '/progress', { progress: progress });
    },

    // --- JARVIS Evolution ---
    goals: function () { return _get('/goals'); },
    goalDetail: function (goalId) {
      return _get('/goals/' + encodeURIComponent(goalId));
    },
    profile: function () { return _get('/profile'); },
    proactivity: function () { return _get('/proactivity'); },
    perception: function () { return _get('/perception'); },
    integrations: function () { return _get('/integrations'); },

    // --- Skills (Fase B) ---
    skills: function () { return _get('/skills'); },
    skillDetail: function (name) {
      return _get('/skills/' + encodeURIComponent(name));
    },
    runSkill: function (name, variables) {
      return _post(
        '/skills/' + encodeURIComponent(name) + '/run',
        { variables: variables || {} },
        true
      );
    },

    // --- Scheduler / Jobs (Fase C) ---
    scheduler: function () { return _get('/scheduler'); },
    schedulerJob: function (jobId) {
      return _get('/scheduler/' + encodeURIComponent(jobId));
    },
    createJob: function (name, kind, schedule, params, enabled) {
      return _post('/scheduler', {
        name: name,
        kind: kind,
        schedule: schedule,
        params: params || {},
        enabled: enabled !== false
      }, true);
    },
    schedulerAction: function (jobId, action) {
      // action: enable | disable | delete | run
      return _post(
        (action === 'run' ? '/scheduler/' + encodeURIComponent(jobId) + '/run'
          : '/scheduler/' + encodeURIComponent(jobId)),
        action === 'run' ? {} : { action: action },
        true
      );
    },

    // --- Agentes (Fase C) ---
    agents: function () { return _get('/agents'); },
    agentDetail: function (agentId) {
      return _get('/agents/' + encodeURIComponent(agentId));
    },
    createAgent: function (name, role, objective, schedule, enabled) {
      return _post('/agents', {
        name: name,
        role: role,
        objective: objective,
        schedule: schedule || '',
        enabled: enabled !== false
      }, true);
    },
    agentAction: function (agentId, action, fields) {
      // action: enable | disable | delete | update | run
      var body = {};
      if (action === 'run') return _post('/agents/' + encodeURIComponent(agentId) + '/run', {}, true);
      if (action === 'update') body = { action: 'update', name: fields.name, role: fields.role, objective: fields.objective, schedule: fields.schedule };
      else body = { action: action };
      return _post('/agents/' + encodeURIComponent(agentId), body, true);
    },

    // --- Deep Research (Fase D) ---
    researchTasks: function () { return _get('/research'); },
    researchTask: function (taskId) {
      return _get('/research/' + encodeURIComponent(taskId));
    },
    runResearch: function (question) {
      return _post('/research/run', { question: question }, true);
    },

    // --- Memoria (Fase E) ---
    memorySearch: function (term, limit) {
      var path = '/memory/search?q=' + encodeURIComponent(term || '');
      if (limit) path += '&limit=' + encodeURIComponent(String(limit));
      return _get(path);
    },
    memoryRecents: function (type, limit) {
      var path = '/memory/recents';
      var q = [];
      if (type) q.push('type=' + encodeURIComponent(type));
      if (limit) q.push('limit=' + encodeURIComponent(String(limit)));
      if (q.length) path += '?' + q.join('&');
      return _get(path);
    },
    memoryStats: function () { return _get('/memory'); },
    memoryIngest: function (source, tag) {
      return _post('/memory/ingest', { source: source, tag: tag || '' }, true);
    },
    memoryConsolidate: function () {
      return _post('/memory/consolidate', {}, true);
    },

    // --- Diagnostics + Benchmark (Fase F) ---
    diagnostics: function () { return _get('/diagnostics'); },
    diagnosticsCheck: function () { return _get('/diagnostics/check'); },
    benchmark: function () { return _get('/benchmark'); },
    runBenchmark: function (mode, categories) {
      return _post('/benchmark/run', {
        mode: mode || 'offline',
        categories: categories || undefined
      }, true);
    },

    // --- Seguridad / Permisos ---
    security: function () { return _get('/security'); },
    grants: function (params) {
      var path = '/grants';
      var q = [];
      if (params && params.capability) q.push('capability=' + encodeURIComponent(params.capability));
      if (params && params.status) q.push('status=' + encodeURIComponent(params.status));
      if (q.length) path += '?' + q.join('&');
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