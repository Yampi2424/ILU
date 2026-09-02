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

  async function _get(path) {
    try {
      const response = await fetch(BASE + path, {
        method: 'GET',
        headers: { 'Accept': 'application/json' }
      });
      return await response.json();
    } catch (error) {
      console.error(`[I.L.U. API] GET ${path}:`, error);
      return { error: 'network_error', detail: String(error) };
    }
  }

  async function _post(path, body) {
    try {
      const response = await fetch(BASE + path, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json'
        },
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
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json'
        },
        body: JSON.stringify(body)
      });
      return await response.json();
    } catch (error) {
      console.error(`[I.L.U. API] PUT ${path}:`, error);
      return { error: 'network_error', detail: String(error) };
    }
  }

  async function _delete(path) {
    try {
      const response = await fetch(BASE + path, {
        method: 'DELETE',
        headers: { 'Accept': 'application/json' }
      });
      return await response.json();
    } catch (error) {
      console.error(`[I.L.U. API] DELETE ${path}:`, error);
      return { error: 'network_error', detail: String(error) };
    }
  }

  return {
    // --- Estado ---
    healthz: function () { return _get('/healthz'); },
    about: function () { return _get('/about'); },

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
      return _delete('/conversations/' + encodeURIComponent(sessionId || 'default'));
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

    resolveAuthRequest: function (requestId, actor, decision, reason) {
      return _post(
        '/authorization-requests/' + encodeURIComponent(requestId),
        { actor: actor, decision: decision, reason: reason || '' }
      );
    },

    grantPermission: function (actor, capability, reason) {
      return _post('/grants', {
        actor: actor,
        capability: capability,
        reason: reason || ''
      });
    },

    changeAutonomy: function (actor, level) {
      return _post('/autonomy', { actor: actor, level: level });
    }
  };
})();
