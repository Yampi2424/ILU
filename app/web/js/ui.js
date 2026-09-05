/**
 * I.L.U. — Interfaz de usuario
 *
 * Gestiona la navegación entre vistas, paneles modales,
 * la barra lateral, y la visualización de datos del backend.
 *
 * PRINCIPIO: este módulo SOLO muestra datos y transmite órdenes.
 * NUNCA toma decisiones de autoridad ni ejecuta permisos.
 */

window.ILUUI = (function () {
  'use strict';

  let _currentView = 'chat';
  let _sidebarVisible = true;

  // --- Utilidades ---------------------------------------------------

  function _escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  function _formatTime(isoString) {
    if (!isoString) return '';
    try {
      const d = new Date(isoString);
      return d.toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit' });
    } catch (_) {
      return '';
    }
  }

  // --- Navegación de vistas -----------------------------------------

  function switchView(view) {
    const views = ['chat', 'tasks', 'permissions', 'about'];

    views.forEach(function (v) {
      const el = document.getElementById('view' + v.charAt(0).toUpperCase() + v.slice(1));
      if (el) el.style.display = v === view ? '' : 'none';
    });

    document.querySelectorAll('.topbar-btn[data-view]').forEach(function (btn) {
      btn.classList.toggle('active', btn.getAttribute('data-view') === view);
    });

    _currentView = view;

    // Carga datos al cambiar de vista
    if (view === 'tasks') _loadTasks();
    if (view === 'permissions') _loadPermissions();
    if (view === 'about') _loadAbout();
  }

  // --- Sidebar ------------------------------------------------------

  function toggleSidebar() {
    const sidebar = document.getElementById('sidebar');
    if (!sidebar) return;

    _sidebarVisible = !_sidebarVisible;
    sidebar.classList.toggle('collapsed', !_sidebarVisible);
  }

  // --- Chat ---------------------------------------------------------

  function appendMessage(role, text, meta) {
    const container = document.getElementById('chatMessages');
    if (!container) return;

    const msg = document.createElement('div');
    msg.className = 'chat-msg ' + role;

    const content = document.createElement('div');
    content.textContent = text;
    msg.appendChild(content);

    if (meta) {
      const metaEl = document.createElement('div');
      metaEl.className = 'msg-meta';
      metaEl.textContent = meta;
      msg.appendChild(metaEl);
    }

    container.appendChild(msg);
    container.scrollTop = container.scrollHeight;
  }

  function showTypingIndicator() {
    const container = document.getElementById('chatMessages');
    if (!container) return;

    const existing = document.getElementById('typingIndicator');
    if (existing) return;

    const msg = document.createElement('div');
    msg.className = 'chat-msg assistant';
    msg.id = 'typingIndicator';
    msg.innerHTML = '<span class="loading-dots">I.L.U. piensa</span>';
    container.appendChild(msg);
    container.scrollTop = container.scrollHeight;
  }

  /**
   * Mensaje de usuario "en vivo": muestra en el chat lo que I.L.U.
   * está escuchando mientras el usuario habla (transcripción parcial).
   * Se actualiza en cada resultado intermedio y se consolida al final
   * del turno (clearLiveUserMessage).
   */
  function liveUserMessage(text) {
    const container = document.getElementById('chatMessages');
    if (!container) return;

    let live = document.getElementById('liveUserMsg');
    if (!live) {
      live = document.createElement('div');
      live.id = 'liveUserMsg';
      live.className = 'chat-msg user live';
      const content = document.createElement('div');
      live.appendChild(content);
      container.appendChild(live);
    }
    live.querySelector('div').textContent = text;
    container.scrollTop = container.scrollHeight;
  }

  function clearLiveUserMessage() {
    const live = document.getElementById('liveUserMsg');
    if (live) live.remove();
  }

  function removeTypingIndicator() {
    const el = document.getElementById('typingIndicator');
    if (el) el.remove();
  }

  // --- Sidebar: memoria, herramienta, subagente, proveedor ----------

  function updateSidebarContext(context) {
    const el = document.getElementById('sidebarMemory');
    if (!el) return;

    if (!context) {
      el.innerHTML = '<div class="sidebar-empty">Sin contexto aún</div>';
      return;
    }

    const items = context.split(' | ').filter(Boolean);
    if (items.length === 0) {
      el.innerHTML = '<div class="sidebar-empty">Sin contexto aún</div>';
      return;
    }

    el.innerHTML = items.map(function (item) {
      return '<div class="sidebar-item">'
        + '<div class="sidebar-item-icon">💡</div>'
        + '<div class="sidebar-item-label">' + _escapeHtml(item) + '</div>'
        + '</div>';
    }).join('');
  }

  function updateSidebarTool(toolName, toolResult) {
    const el = document.getElementById('sidebarTool');
    if (!el) return;

    if (!toolName) {
      el.innerHTML = '<div class="sidebar-empty">Ninguna</div>';
      return;
    }

    let detail = '';
    if (toolResult && typeof toolResult === 'object') {
      detail = JSON.stringify(toolResult, null, 2);
    } else if (toolResult) {
      detail = String(toolResult);
    }

    el.innerHTML = '<div class="sidebar-item">'
      + '<div class="sidebar-item-icon">🔧</div>'
      + '<div class="sidebar-item-label">' + _escapeHtml(toolName) + '</div>'
      + '</div>'
      + (detail
        ? '<pre style="font-size:11px; color:var(--ilu-text-dim); padding:4px 8px; white-space:pre-wrap; max-height:120px; overflow:auto; font-family:var(--ilu-font-mono);">'
          + _escapeHtml(detail.substring(0, 300))
          + '</pre>'
        : '');
  }

  function updateSidebarSubagent(subagent) {
    const el = document.getElementById('sidebarSubagent');
    if (!el) return;

    if (!subagent) {
      el.innerHTML = '<div class="sidebar-empty">Inactivo</div>';
      return;
    }

    el.innerHTML = '<div class="sidebar-item">'
      + '<div class="sidebar-item-icon">🤖</div>'
      + '<div class="sidebar-item-label">Sub-agente activo</div>'
      + '</div>'
      + '<div style="font-size:12px; color:var(--ilu-text-dim); padding:4px 8px;">'
      + 'Rondas: ' + (subagent.rounds || 0)
      + (subagent.tools_used && subagent.tools_used.length
        ? ' · Tools: ' + subagent.tools_used.join(', ')
        : '')
      + '</div>';
  }

  function updateSidebarProvider(provider) {
    const el = document.getElementById('sidebarProvider');
    if (!el) return;

    if (!provider) {
      el.innerHTML = '<div class="sidebar-empty">Desconocido</div>';
      return;
    }

    const fallback = provider.fallback
      ? ' <span style="color:var(--ilu-warm);">(fallback)</span>'
      : '';

    el.innerHTML = '<div class="sidebar-item">'
      + '<div class="sidebar-item-icon">⚡</div>'
      + '<div class="sidebar-item-label">'
      + _escapeHtml(provider.name || '?')
      + ' v' + _escapeHtml(provider.version || '?')
      + fallback
      + '</div>'
      + '</div>';
  }

  // --- Modo de autonomía -------------------------------------------

  function updateModeBadge(mode) {
    const badge = document.getElementById('modeBadge');
    const text = document.getElementById('modeText');
    if (!badge || !text) return;

    const labels = {
      manual: 'Manual',
      assisted: 'Asistido',
      autonomous: 'Autónomo'
    };

    badge.setAttribute('data-mode', mode || 'manual');
    text.textContent = labels[mode] || mode || 'Manual';
  }

  // --- Panel modal --------------------------------------------------

  function openPanel(title, bodyHtml) {
    const overlay = document.getElementById('panelOverlay');
    const titleEl = document.getElementById('panelTitle');
    const bodyEl = document.getElementById('panelBody');

    if (!overlay) return;

    if (titleEl) titleEl.textContent = title;
    if (bodyEl) bodyEl.innerHTML = bodyHtml;
    overlay.classList.add('visible');
  }

  function closePanel() {
    const overlay = document.getElementById('panelOverlay');
    if (overlay) overlay.classList.remove('visible');
  }

  // --- Vista: Tareas ------------------------------------------------

  async function _loadTasks() {
    const el = document.getElementById('tasksList');
    if (!el) return;

    const data = await ILUApi.tasks();

    if (data.error || !data.tasks || data.tasks.length === 0) {
      el.innerHTML = '<div class="empty-state">'
        + '<div class="empty-state-icon">📋</div>'
        + '<div>No hay tareas registradas</div>'
        + '</div>';
      return;
    }

    el.innerHTML = '<table class="data-table">'
      + '<thead><tr>'
      + '<th>Título</th><th>Estado</th><th>Progreso</th><th>ID</th>'
      + '</tr></thead>'
      + '<tbody>'
      + data.tasks.map(function (t) {
        return '<tr>'
          + '<td>' + _escapeHtml(t.title || '') + '</td>'
          + '<td><span class="status-badge ' + (t.state || 'pending') + '">'
          + (t.state || 'pending') + '</span></td>'
          + '<td>' + (t.progress || 0) + '%</td>'
          + '<td style="font-family:var(--ilu-font-mono); font-size:11px;">'
          + _escapeHtml((t.id || '').substring(0, 8)) + '</td>'
          + '</tr>';
      }).join('')
      + '</tbody></table>';
  }

  // --- Vista: Permisos ----------------------------------------------

  async function _loadPermissions() {
    const secData = await ILUApi.security();

    // Autonomía
    const autonomyEl = document.getElementById('permAutonomy');
    if (autonomyEl && !secData.error) {
      const level = secData.autonomy || 'manual';
      const labels = {
        manual: 'Modo manual — I.L.U. propone, no ejecuta sin ti',
        assisted: 'Modo asistido — I.L.U. ejecuta lo seguro, pide lo que necesita',
        autonomous: 'Modo autónomo — I.L.U. actúa con autorización activa'
      };
      autonomyEl.innerHTML = '<span style="font-weight:600;">'
        + (labels[level] || level) + '</span>'
        + '<div style="margin-top:8px; font-size:12px; color:var(--ilu-text-dim);">'
        + 'Owner: ' + (secData.owner || '?')
        + ' · Principals: ' + (secData.principals || 0)
        + ' · Grants activos: ' + (secData.grants_active || 0)
        + '</div>';

      updateModeBadge(level);

      // Resaltar botón activo
      document.querySelectorAll('#autonomyButtons .action-btn').forEach(function (btn) {
        btn.classList.toggle('primary', btn.getAttribute('data-level') === level);
      });
    }

    // Solicitudes abiertas
    const reqData = await ILUApi.authorizationRequests();
    const reqEl = document.getElementById('permRequests');
    if (reqEl && !reqData.error) {
      const requests = (reqData.requests || []).filter(function (r) {
        return r.status === 'open' || r.status === 'pending';
      });

      if (requests.length === 0) {
        reqEl.innerHTML = '<div class="empty-state"><div>No hay solicitudes abiertas</div></div>';
      } else {
        reqEl.innerHTML = requests.map(function (r) {
          return '<div style="padding:8px; border-bottom:1px solid var(--ilu-border);">'
            + '<div style="display:flex; justify-content:space-between; align-items:center;">'
            + '<div>'
            + '<strong>' + _escapeHtml(r.capability || '?') + '</strong>'
            + '<div style="font-size:12px; color:var(--ilu-text-dim);">'
            + _escapeHtml(r.reason || 'Sin razón')
            + '</div></div>'
            + '<div style="display:flex; gap:4px; flex-wrap:wrap;">'
            + '<button class="action-btn primary" '
            + 'onclick="ILUUI.resolveAuth(\'' + _escapeHtml(r.key || r.request_id || '') + '\', \'granted\')">'
            + 'Conceder</button>'
            + '<button class="action-btn" title="Concede una vez y lo recuerda: I.L.U. podrá usarlo sin volver a preguntar" '
            + 'onclick="ILUUI.resolveAuth(\'' + _escapeHtml(r.key || r.request_id || '') + '\', \'granted\', true)">'
            + 'Conceder y recordar</button>'
            + '<button class="action-btn danger" '
            + 'onclick="ILUUI.resolveAuth(\'' + _escapeHtml(r.key || r.request_id || '') + '\', \'denied\')">'
            + 'Denegar</button>'
            + '</div></div></div>';
        }).join('');
      }
    }

    // Grants activos
    const grantData = await ILUApi.grants({ status: 'active' });
    const grantEl = document.getElementById('permGrants');
    if (grantEl && !grantData.error) {
      const grants = grantData.grants || [];

      if (grants.length === 0) {
        grantEl.innerHTML = '<div class="empty-state"><div>No hay permisos concedidos</div></div>';
      } else {
        grantEl.innerHTML = grants.map(function (g) {
          return '<div class="sidebar-item">'
            + '<div class="sidebar-item-icon">🔑</div>'
            + '<div class="sidebar-item-label">'
            + '<strong>' + _escapeHtml(g.capability || '?') + '</strong>'
            + ' <span style="color:var(--ilu-text-dim);">→ '
            + _escapeHtml(g.grantee || '?') + '</span>'
            + '</div>'
            + '<div class="sidebar-item-badge">'
            + (g.expires_at ? 'exp: ' + _formatTime(g.expires_at) : '∞')
            + '</div>'
            + '</div>';
        }).join('');
      }
    }
  }

  /**
   * Devuelve el PIN del owner para esta sesión de navegador. Si todavía
   * no está cargado, lo pide por prompt y lo guarda en sessionStorage
   * (NO persiste entre sesiones). Es el MISMO secreto de la concesión
   * por voz/texto; la clave jamás se envía al modelo.
   */
  function _ownerPin() {
    let pin = ILUApi.getPin();
    if (!pin) {
      pin = prompt('Clave de autorización (PIN del owner):');
      if (pin) pin = String(pin).trim();
      if (pin) ILUApi.setPin(pin);
    }
    return pin || null;
  }

  function _alertAdminError(result) {
    if (result && result.error === 'unauthorized') {
      // El servidor rechazó la credencial. La clave quedó cacheada en
      // sessionStorage; se invalida para que el próximo intento vuelva
      // a pedirla (en vez de reenviar la clave rechazada en bucle).
      if (ILUApi.hasPin()) ILUApi.setPin('');
      alert(
        'No autorizado. Configurá el token de dispositivo (F12 → Consola → '
        + "ILUApi.setToken('…')) o la clave del owner (ILUApi.setPin('…'))."
      );
    } else {
      alert('Error: ' + ((result && result.error) || 'desconocido'));
    }
  }

  async function resolveAuth(requestId, decision, remember) {
    const actor = prompt('Tu identidad (actor):');
    if (!actor) return;

    // La identidad por sí sola no alcanza: la acción admin demuestra
    // también el secreto del owner (igual que la voz/texto).
    const pin = _ownerPin();
    if (!pin) return;

    const result = await ILUApi.resolveAuthRequest(
      requestId,
      actor,
      decision,
      decision === 'granted'
        ? (remember ? 'Concedido y recordado desde la interfaz' : 'Concedido desde la interfaz')
        : 'Denegado desde la interfaz',
      remember ? { remember: true, indefinite: true } : undefined
    );

    if (result.success) {
      _loadPermissions();
    } else {
      _alertAdminError(result);
    }
  }

  async function changeAutonomy(level) {
    const actor = prompt('Tu identidad (actor):');
    if (!actor) return;

    const pin = _ownerPin();
    if (!pin) return;

    const result = await ILUApi.changeAutonomy(actor, level);

    if (result.success) {
      _loadPermissions();
      updateModeBadge(level);
    } else {
      _alertAdminError(result);
    }
  }

  // --- Vista: About / Identidad -------------------------------------

  async function _loadAbout() {
    const el = document.getElementById('aboutContent');
    if (!el) return;

    const data = await ILUApi.about();

    if (data.error) {
      el.innerHTML = '<div class="empty-state"><div>Error al cargar identidad</div></div>';
      return;
    }

    el.innerHTML = '<div style="text-align:center; margin-bottom:24px;">'
      + '<div style="font-size:32px; font-weight:700; letter-spacing:0.04em;">'
      + _escapeHtml(data.name || 'I.L.U.') + '</div>'
      + '<div style="font-size:14px; color:var(--ilu-text-secondary); margin-top:4px;">'
      + _escapeHtml(data.description || '') + '</div>'
      + '<div style="font-size:12px; color:var(--ilu-text-dim); margin-top:8px;">'
      + 'v' + _escapeHtml(data.version || '?')
      + ' · ' + _escapeHtml(data.mode || '?')
      + '</div></div>'

      + '<div style="background:var(--ilu-bg-card); border:1px solid var(--ilu-border); border-radius:var(--ilu-radius); padding:16px; margin-bottom:16px;">'
      + '<div class="sidebar-title">Rol</div>'
      + '<div style="color:var(--ilu-text-secondary); font-size:14px;">'
      + _escapeHtml(data.role || '') + '</div></div>'

      + '<div style="background:var(--ilu-bg-card); border:1px solid var(--ilu-border); border-radius:var(--ilu-radius); padding:16px; margin-bottom:16px;">'
      + '<div class="sidebar-title">Owner</div>'
      + '<div style="color:var(--ilu-text-secondary); font-size:14px;">'
      + _escapeHtml(data.owner || '') + '</div></div>'

      + '<div style="background:var(--ilu-bg-card); border:1px solid var(--ilu-border); border-radius:var(--ilu-radius); padding:16px; margin-bottom:16px;">'
      + '<div class="sidebar-title">Arquitectura</div>'
      + '<div style="color:var(--ilu-text-secondary); font-size:14px;">'
      + _escapeHtml(data.architecture || '') + '</div></div>'

      + '<div style="background:var(--ilu-bg-card); border:1px solid var(--ilu-border); border-radius:var(--ilu-radius); padding:16px; margin-bottom:16px;">'
      + '<div class="sidebar-title">Capacidades</div>'
      + (data.capabilities || []).map(function (c) {
        return '<div class="sidebar-item">'
          + '<div class="sidebar-item-icon">✦</div>'
          + '<div class="sidebar-item-label">' + _escapeHtml(c) + '</div>'
          + '</div>';
      }).join('') + '</div>'

      + '<div style="background:var(--ilu-bg-card); border:1px solid var(--ilu-border); border-radius:var(--ilu-radius); padding:16px;">'
      + '<div class="sidebar-title">Límites</div>'
      + (data.limits || []).map(function (l) {
        return '<div class="sidebar-item">'
          + '<div class="sidebar-item-icon">⚠</div>'
          + '<div class="sidebar-item-label">' + _escapeHtml(l) + '</div>'
          + '</div>';
      }).join('') + '</div>';
  }

  return {
    switchView: switchView,
    toggleSidebar: toggleSidebar,
    appendMessage: appendMessage,
    showTypingIndicator: showTypingIndicator,
    removeTypingIndicator: removeTypingIndicator,
    liveUserMessage: liveUserMessage,
    clearLiveUserMessage: clearLiveUserMessage,
    updateSidebarContext: updateSidebarContext,
    updateSidebarTool: updateSidebarTool,
    updateSidebarSubagent: updateSidebarSubagent,
    updateSidebarProvider: updateSidebarProvider,
    updateModeBadge: updateModeBadge,
    openPanel: openPanel,
    closePanel: closePanel,
    resolveAuth: resolveAuth,
    changeAutonomy: changeAutonomy,
    _escapeHtml: _escapeHtml
  };
})();
