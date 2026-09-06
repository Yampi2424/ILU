/**
 * I.L.U. — Interfaz de usuario (shell femenino-plasma)
 *
 * Paneles elegantes en drawers de vidrio (Tasks, Agentes, Skills,
 * Memoria, Actividad, Permisos, Sistema), PIN modal sin prompt(),
 * toasts de notificación y construcción de mensajes del chat.
 *
 * PRINCIPIO: este módulo SOLO muestra datos y transmite órdenes.
 * NUNCA toma decisiones de autoridad ni ejecuta permisos. Las acciones
 * administrativas demuestran la identidad del owner (prefill desde
 * /security) y el secreto vía el modal de PIN.
 */

window.ILUUI = (function () {
  'use strict';

  let _currentPanel = null;

  // --- Utilidades ----------------------------------------------------

  function _esc(text) {
    const div = document.createElement('div');
    div.textContent = String(text == null ? '' : text);
    return div.innerHTML;
  }

  function _fmtTime(isoString) {
    if (!isoString) return '';
    try {
      const d = new Date(isoString);
      return d.toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit' });
    } catch (_) {
      return '';
    }
  }

  function _fmtDate(isoString) {
    if (!isoString) return '';
    try {
      const d = new Date(isoString);
      return d.toLocaleDateString('es-AR', { day: '2-digit', month: 'short' });
    } catch (_) {
      return '';
    }
  }

  function _el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function _toast(message, kind, ttl) {
    const container = document.getElementById('toastContainer');
    if (!container) return;
    const toast = _el('div', 'toast ' + (kind || 'info'));
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => toast.classList.add('show'), 20);
    setTimeout(() => {
      toast.classList.remove('show');
      setTimeout(() => toast.remove(), 400);
    }, ttl || 5000);
  }

  // --- Chat ----------------------------------------------------------

  function appendMessage(role, text, meta) {
    const container = document.getElementById('chatMessages');
    if (!container) return;

    const msg = _el('div', 'chat-msg ' + role);

    const content = _el('div', 'chat-msg-content');
    content.textContent = text;
    msg.appendChild(content);

    if (meta) {
      const metaEl = _el('div', 'msg-meta');
      metaEl.textContent = meta;
      msg.appendChild(metaEl);
    }

    container.appendChild(msg);
    container.scrollTop = container.scrollHeight;
    return msg.id ? null : _wireLiveId(msg);
  }

  function _wireLiveId(msg) {
    // Indign template: asignamos un id para updateMessage/setMessageMeta.
    const id = 'msg-' + Date.now() + '-' + Math.floor(Math.random() * 1e4);
    msg.id = id;
    msg.setAttribute('data-live', '1');
    return id;
  }

  function showTypingIndicator() {
    const container = document.getElementById('chatMessages');
    if (!container) return;
    const existing = document.getElementById('typingIndicator');
    if (existing) return;
    const msg = _el('div', 'chat-msg assistant typing');
    msg.id = 'typingIndicator';
    msg.innerHTML = '<span class="loading-dots"></span><span class="typing-text">I.L.U. piensa</span>';
    container.appendChild(msg);
    container.scrollTop = container.scrollHeight;
  }

  function removeTypingIndicator() {
    const el = document.getElementById('typingIndicator');
    if (el) el.remove();
  }

  function liveUserMessage(text) {
    const container = document.getElementById('chatMessages');
    if (!container) return;
    let live = document.getElementById('liveUserMsg');
    if (!live) {
      live = _el('div', 'chat-msg user live');
      live.id = 'liveUserMsg';
      live.appendChild(_el('div', 'chat-msg-content'));
      container.appendChild(live);
    }
    live.querySelector('.chat-msg-content').textContent = text;
    container.scrollTop = container.scrollHeight;
  }

  function clearLiveUserMessage() {
    const live = document.getElementById('liveUserMsg');
    if (live) live.remove();
  }

  function updateMessage(msgId, text) {
    const msg = document.getElementById(msgId);
    if (!msg) return;
    const content = msg.querySelector('.chat-msg-content');
    if (content) content.textContent = text;
  }

  function setMessageMeta(msgId, meta) {
    const msg = document.getElementById(msgId);
    if (!msg) return;
    let metaEl = msg.querySelector('.msg-meta');
    if (!metaEl && meta) {
      metaEl = _el('div', 'msg-meta');
      msg.appendChild(metaEl);
    }
    if (metaEl) metaEl.textContent = meta || '';
  }

  // --- Sidebar / mini-status (lenguaje natural) ----------------------

  function updateSidebarContext(context) {
    const el = document.getElementById('stateHint');
    if (!el || !context) return;
    const items = context.split(' | ').filter(Boolean);
    el.textContent = items.join(' · ');
  }

  function updateSidebarTool(toolName) {
    if (!toolName) return;
    const el = document.getElementById('miniStatus');
    if (el) el.textContent = 'Usé ' + toolName;
  }

  function updateSidebarSubagent(subagent) {
    if (!subagent) return;
    const el = document.getElementById('miniStatus');
    if (el) el.textContent = 'Sub-agente · ' + (subagent.rounds || 0) + ' rondas';
  }

  function updateSidebarProvider(provider) {
    if (!provider) return;
    const el = document.getElementById('chatStatus');
    if (!el) return;
    el.textContent = provider.name || '';
  }

  // --- Modo de autonomía -------------------------------------------

  function updateModeBadge(mode) {
    const el = document.getElementById('chatStatus');
    const labels = {
      manual: 'Manual',
      assisted: 'Asistido',
      autonomous: 'Autónomo'
    };
    if (el && mode) el.textContent = labels[mode] || mode;
  }

  // --- Drawer (paneles) ----------------------------------------------

  const PANEL_TITLES = {
    tasks: 'Tareas',
    agents: 'Agentes',
    skills: 'Skills',
    memory: 'Memoria',
    activity: 'Actividad',
    permissions: 'Permisos',
    settings: 'Sistema'
  };

  const PANEL_LOADERS = {
    tasks: _loadTasks,
    agents: _loadAgents,
    skills: _loadSkills,
    memory: _loadMemory,
    activity: _loadActivity,
    permissions: _loadPermissions,
    settings: _loadSettings
  };

  function openDrawer(panel) {
    if (!PANEL_LOADERS[panel]) return;
    _currentPanel = panel;

    const overlay = document.getElementById('panelDrawerOverlay');
    const drawer = document.getElementById('panelDrawer');
    const titleEl = document.getElementById('panelDrawerTitle');
    const body = document.getElementById('panelDrawerBody');

    if (titleEl) titleEl.textContent = PANEL_TITLES[panel] || panel;
    if (body) {
      body.innerHTML = '<div class="panel-loading"><span class="pulse-dot"></span> Cargando…</div>';
    }

    overlay.classList.add('visible');
    overlay.setAttribute('aria-hidden', 'false');
    drawer.classList.add('visible');
    drawer.setAttribute('aria-modal', 'true');

    PANEL_LOADERS[panel](body);
  }

  function closeDrawer() {
    const overlay = document.getElementById('panelDrawerOverlay');
    const drawer = document.getElementById('panelDrawer');
    if (overlay) { overlay.classList.remove('visible'); overlay.setAttribute('aria-hidden', 'true'); }
    if (drawer) { drawer.classList.remove('visible'); drawer.setAttribute('aria-modal', 'false'); }
    _currentPanel = null;
  }

  function _drawerHeader(icon, title, onRefresh) {
    const row = _el('div', 'drawer-toolbar');
    const left = _el('div', 'drawer-toolbar-left');
    left.innerHTML = icon + '<span>' + _esc(title) + '</span>';
    row.appendChild(left);
    const refresh = _el('button', 'drawer-toolbar-btn');
    refresh.textContent = 'Refrescar';
    refresh.addEventListener('click', function () {
      if (onRefresh) onRefresh(document.getElementById('panelDrawerBody'));
    });
    row.appendChild(refresh);
    return row;
  }

  function _card(icon, title, bodyHtml, extra) {
    const card = _el('div', 'glass-card' + (extra ? ' ' + extra : ''));
    const head = _el('div', 'glass-card-head');
    head.innerHTML = '<span class="glass-card-icon">' + (icon || '') + '</span>'
      + '<strong class="glass-card-title">' + _esc(title) + '</strong>';
    card.appendChild(head);
    const bodyWrap = _el('div', 'glass-card-body');
    if (bodyHtml) {
      bodyWrap.innerHTML = bodyHtml;
    }
    card.appendChild(bodyWrap);
    return card;
  }

  function _empty(text) {
    const el = _el('div', 'empty-state');
    el.innerHTML = '<span class="empty-orb">✦</span><span>' + _esc(text) + '</span>';
    return el;
  }

  // --- Panel: Tareas --------------------------------------------------

  async function _loadTasks(body) {
    const data = await ILUApi.tasks();
    body.innerHTML = '';
    body.appendChild(_drawerHeader('📋', 'Tareas · ' + (data.count || 0), _loadTasks));

    const row = _el('div', 'panel-row');
    const input = _el('input', 'panel-input');
    input.placeholder = 'Nueva tarea…';
    input.setAttribute('aria-label', 'Nueva tarea');
    const btn = _el('button', 'panel-btn primary');
    btn.textContent = 'Crear';
    btn.addEventListener('click', async function () {
      const title = input.value.trim();
      if (!title) return;
      await ILUApi.createTask(title);
      input.value = '';
      _loadTasks(body);
    });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') btn.click();
    });
    row.appendChild(input);
    row.appendChild(btn);
    body.appendChild(row);

    if (data.error || !data.tasks || data.tasks.length === 0) {
      body.appendChild(_empty('No hay tareas registradas'));
      return;
    }

    const list = _el('div', 'panel-list');
    data.tasks.slice(0, 40).forEach(function (t) {
      const pill = _el('span', 'status-badge ' + (t.state || 'pending'));
      pill.textContent = t.state || 'pending';

      const progress = _el('div', 'task-progress');
      progress.innerHTML = '<div class="progress-track"><div class="progress-fill" style="width:' + (t.progress || 0) + '%"></div></div>'
        + '<span>' + (t.progress || 0) + '%</span>';

      const meta = _el('div', 'glass-card-text');
      meta.textContent = _esc(t.id ? t.id.substring(0, 8) : '') + (t.created_at ? ' · ' + _fmtTime(t.created_at) : '');

      const actions = _el('div', 'panel-btn-row');
      const doneBtn = _el('button', 'panel-btn small');
      doneBtn.textContent = 'Completar';
      doneBtn.disabled = t.state === 'completed';
      doneBtn.addEventListener('click', async function () {
        await ILUApi.updateTaskState(t.id, 'completed');
        _loadTasks(body);
      });
      actions.appendChild(doneBtn);

      const card = _card('✔', t.title, '', 'task-card');
      card.querySelector('.glass-card-body').appendChild(pill);
      card.querySelector('.glass-card-body').appendChild(progress);
      card.querySelector('.glass-card-body').appendChild(meta);
      card.querySelector('.glass-card-body').appendChild(actions);
      list.appendChild(card);
    });
    body.appendChild(list);
  }

  // --- Panel: Agentes ------------------------------------------------

  function _agentCard(agent, body) {
    const card = _card('◈', agent.name, '', 'agent-card');
    const b = card.querySelector('.glass-card-body');

    const sub = _el('div', 'glass-card-text');
    sub.textContent = (agent.role || '') + (agent.objective ? ' — ' + agent.objective : '');
    b.appendChild(sub);

    const meta = _el('div', 'glass-card-text dim');
    meta.textContent = (agent.schedule ? '🕐 ' + agent.schedule : 'sin agenda')
      + (agent.last_run ? ' · última: ' + _fmtTime(agent.last_run) : '');
    b.appendChild(meta);

    const pill = _el('span', 'status-badge ' + (agent.enabled ? 'active' : 'disabled'));
    pill.textContent = agent.enabled ? 'activo' : 'pausado';
    b.appendChild(pill);

    const actions = _el('div', 'panel-btn-row');
    const runBtn = _el('button', 'panel-btn small primary');
    runBtn.textContent = 'Correr';
    runBtn.addEventListener('click', function () {
      _withPin(function (pin) { return ILUApi.agentAction(agent.id, 'run'); }).then(function (r) {
        if (r.error) { _withPinAdminError('agent', r); return; }
        _toast('Agente "' + agent.name + '" lanzado', 'ok');
        _loadAgents(body);
      }).catch(function () { _loadAgents(body); });
    });
    actions.appendChild(runBtn);

    if (agent.enabled) {
      const pauseBtn = _el('button', 'panel-btn small');
      pauseBtn.textContent = 'Pausar';
      pauseBtn.addEventListener('click', function () {
        _withPin(function (pin) { return ILUApi.agentAction(agent.id, 'disable'); }).then(function (r) {
          if (r.error) { _withPinAdminError('agent', r); return; }
          _loadAgents(body);
        }).catch(function () { _loadAgents(body); });
      });
      actions.appendChild(pauseBtn);
    } else {
      const actBtn = _el('button', 'panel-btn small');
      actBtn.textContent = 'Activar';
      actBtn.addEventListener('click', function () {
        _withPin(function (pin) { return ILUApi.agentAction(agent.id, 'enable'); }).then(function (r) {
          if (r.error) { _withPinAdminError('agent', r); return; }
          _loadAgents(body);
        }).catch(function () { _loadAgents(body); });
      });
      actions.appendChild(actBtn);
    }

    const delBtn = _el('button', 'panel-btn small danger');
    delBtn.textContent = 'Eliminar';
    delBtn.addEventListener('click', function () {
      _withPin(function (pin) { return ILUApi.agentAction(agent.id, 'delete'); }).then(function (r) {
        if (r.error) { _withPinAdminError('agent', r); return; }
        _loadAgents(body);
      }).catch(function () { _loadAgents(body); });
    });
    actions.appendChild(delBtn);
    b.appendChild(actions);
    return card;
  }

  async function _loadAgents(body) {
    const data = await ILUApi.agents();
    body.innerHTML = '';
    body.appendChild(_drawerHeader('◈', 'Agentes · ' + (data.count || 0), _loadAgents));

    // Formulario de creación
    const form = _el('div', 'create-form');
    const name = _el('input', 'panel-input');
    name.placeholder = 'Nombre del agente';
    const role = _el('input', 'panel-input');
    role.placeholder = 'Rol (ej. investigador)';
    const objective = _el('input', 'panel-input');
    objective.placeholder = 'Objetivo';
    const schedule = _el('input', 'panel-input');
    schedule.placeholder = 'Agenda (ej. 09:00)';
    const btn = _el('button', 'panel-btn primary');
    btn.textContent = 'Crear agente';
    btn.addEventListener('click', function () {
      const n = name.value.trim(), r = role.value.trim(), o = objective.value.trim();
      if (!n || !r || !o) { _toast('Completá nombre, rol y objetivo', 'warn'); return; }
      _withPin(function (pin) { return ILUApi.createAgent(n, r, o, schedule.value.trim()); }).then(function (res) {
        if (res.error) { _withPinAdminError('agent', res); return; }
        _toast('Agente "' + n + '" creado', 'ok');
        _loadAgents(body);
      }).catch(function () { _loadAgents(body); });
    });
    [name, role, objective, schedule].forEach(function (inp) {
      inp.addEventListener('keydown', function (e) { if (e.key === 'Enter') btn.click(); });
    });
    [name, role, objective, schedule].forEach(function (inp) { form.appendChild(inp); });
    form.appendChild(btn);
    body.appendChild(form);

    if (data.error || !data.agents || data.agents.length === 0) {
      body.appendChild(_empty('No hay agentes creados'));
      return;
    }

    const list = _el('div', 'panel-list');
    data.agents.forEach(function (agent) {
      list.appendChild(_agentCard(agent, body));
    });
    body.appendChild(list);
  }

  // --- Panel: Skills --------------------------------------------------

  async function _loadSkills(body) {
    const data = await ILUApi.skills();
    body.innerHTML = '';
    body.appendChild(_drawerHeader('⚡', 'Skills · ' + (data.count || 0), _loadSkills));

    if (data.error || !data.skills || data.skills.length === 0) {
      body.appendChild(_empty('Sin skills en el catálogo'));
      return;
    }

    const list = _el('div', 'panel-list');
    data.skills.forEach(function (skill) {
      const card = _card('⚡', skill.name, '', 'skill-card');
      const b = card.querySelector('.glass-card-body');

      const desc = _el('div', 'glass-card-text');
      desc.textContent = skill.description || '';
      b.appendChild(desc);

      const steps = skill.steps || {};
      const count = steps.length || 0;
      const meta = _el('div', 'glass-card-text dim');
      meta.textContent = count + (count === 1 ? ' paso' : ' pasos');
      b.appendChild(meta);

      const actions = _el('div', 'panel-btn-row');
      const runBtn = _el('button', 'panel-btn small primary');
      runBtn.textContent = 'Ejecutar';
      runBtn.addEventListener('click', function () {
        _withPin(function (pin) { return ILUApi.runSkill(skill.name, {}); }).then(function (r) {
          if (r.error) { _withPinAdminError('skill', r); return; }
          _toast('Skill "' + skill.name + '" finalizada', 'ok');
          ILUCore.setIntensity && ILUCore.setIntensity(0.8);
        }).catch(function () {});
      });
      actions.appendChild(runBtn);
      b.appendChild(actions);
      list.appendChild(card);
    });
    body.appendChild(list);
  }

  // --- Panel: Memoria ------------------------------------------------

  async function _loadMemory(body) {
    const stats = await ILUApi.memoryStats();
    body.innerHTML = '';
    body.appendChild(_drawerHeader('✦', 'Memoria · ' + (stats.stats || {}).total || 0, _loadMemory));

    // Resumen por tipo
    const types = (stats.types || []).filter(function (t) { return t.count > 0; }) || [];
    const summary = _el('div', 'memory-chips');
    types.slice(0, 12).forEach(function (t) {
      const chip = _el('span', 'memory-chip');
      chip.textContent = t.name + ' · ' + t.count;
      chip.addEventListener('click', function () { _loadMemoryType(body, t.name); });
      summary.appendChild(chip);
    });
    body.appendChild(summary);

    // Búsqueda por significado
    const row = _el('div', 'panel-row');
    const search = _el('input', 'panel-input');
    search.placeholder = 'Buscar por significado…';
    const btn = _el('button', 'panel-btn primary');
    btn.textContent = 'Buscar';
    btn.addEventListener('click', function () {
      const q = search.value.trim();
      if (q) _loadMemorySearch(body, q);
    });
    search.addEventListener('keydown', function (e) { if (e.key === 'Enter') btn.click(); });
    row.appendChild(search);
    row.appendChild(btn);
    body.appendChild(row);

    // Ingesta desde el workspace
    const ingestRow = _el('div', 'panel-row');
    const ingest = _el('input', 'panel-input');
    ingest.placeholder = 'Ruta a ingerir (relativa al workspace)';
    const ingestBtn = _el('button', 'panel-btn');
    ingestBtn.textContent = 'Ingerir';
    ingestBtn.addEventListener('click', function () {
      const src = ingest.value.trim();
      if (!src) { _toast('Indicá una ruta del workspace', 'warn'); return; }
      _withPin(function (pin) { return ILUApi.memoryIngest(src); }).then(function (r) {
        if (r.error) { _withPinAdminError('ingest', r); return; }
        _toast((r.remembered || '') + ' recordada', 'ok');
        _loadMemory(body);
      }).catch(function () { _loadMemory(body); });
    });
    ingest.addEventListener('keydown', function (e) { if (e.key === 'Enter') ingestBtn.click(); });
    ingestRow.appendChild(ingest);
    ingestRow.appendChild(ingestBtn);
    body.appendChild(ingestRow);

    // Consolidación
    const consBtn = _el('button', 'panel-btn ghost');
    consBtn.textContent = 'Consolidar lo aprendido';
    consBtn.addEventListener('click', function () {
      _withPin(function (pin) { return ILUApi.memoryConsolidate(); }).then(function (r) {
        if (r.error) { _withPinAdminError('consolidate', r); return; }
        _toast('Consolidación: ' + (r.consolidated || 0) + ' recuerdos', 'ok');
        ILUCore.set(ILUCore.STATES.CONSOLIDATING);
        setTimeout(function () { ILUCore.set(ILUCore.STATES.IDLE); }, 3000);
        _loadMemory(body);
      }).catch(function () { _loadMemory(body); });
    });
    body.appendChild(consBtn);

    _loadMemoryType(body, 'episodic');
  }

  async function _loadMemorySearch(body, term) {
    const data = await ILUApi.memorySearch(term, 25);
    body.querySelectorAll('.search-results').forEach(function (n) { n.remove(); });

    const wrap = _el('div', 'search-results');
    wrap.appendChild(_drawerHeader('🔎', 'Resultados para "' + term + '"', null));

    if (data.error || !data.results || !data.results.length) {
      wrap.appendChild(_empty('Sin coincidencias'));
    } else {
      const list = _el('div', 'panel-list');
      data.results.forEach(function (m) {
        const card = _card('✦', m.type || 'memoria', '', 'memory-card');
        const b = card.querySelector('.glass-card-body');
        const text = _el('div', 'glass-card-text');
        text.textContent = m.content;
        b.appendChild(text);
        const meta = _el('div', 'glass-card-text dim');
        meta.textContent = 'importancia ' + (m.importance != null ? m.importance : 0)
          + (m.source ? ' · ' + _esc(m.source) : '');
        b.appendChild(meta);
        list.appendChild(card);
      });
      wrap.appendChild(list);
    }
    body.appendChild(wrap);
  }

  async function _loadMemoryType(body, type) {
    const data = await ILUApi.memoryRecents(type, 20);
    body.querySelectorAll('.type-results').forEach(function (n) { n.remove(); });

    const wrap = _el('div', 'type-results');
    wrap.appendChild(_drawerHeader('✦', 'De tipo ' + type, null));

    if (data.error || !data.results || !data.results.length) {
      wrap.appendChild(_empty('Nada de tipo ' + type + ' todavía'));
    } else {
      const list = _el('div', 'panel-list');
      data.results.forEach(function (m) {
        const card = _card('✦', (m.type || type), '', 'memory-card');
        const b = card.querySelector('.glass-card-body');
        const text = _el('div', 'glass-card-text');
        text.textContent = m.content;
        b.appendChild(text);
        const meta = _el('div', 'glass-card-text dim');
        meta.textContent = (m.source ? _esc(m.source) + ' · ' : '') + _fmtDate(m.created_at);
        b.appendChild(meta);
        list.appendChild(card);
      });
      wrap.appendChild(list);
    }
    body.appendChild(wrap);
  }

  // --- Panel: Actividad ----------------------------------------------

  async function _loadActivity(body) {
    body.innerHTML = '';
    body.appendChild(_drawerHeader('◈', 'Actividad en vivo', _loadActivity));

    const [state, proact, percep] = await Promise.all([
      ILUApi.state(),
      ILUApi.proactivity(),
      ILUApi.perception()
    ]);

    if (state && !state.error) {
      if (state.goals && state.goals.length) {
        const card = _card('🎯', 'Objetivos activos', '');
        const b = card.querySelector('.glass-card-body');
        state.goals.forEach(function (g) {
          const goalRow = _el('div', 'row-line');
          goalRow.innerHTML = '<span>' + _esc(g.title) + '</span>'
            + '<div class="progress-track thin"><div class="progress-fill" style="width:' + (g.progress || 0) + '%"></div></div>'
            + '<span class="dim">' + (g.progress || 0) + '%</span>';
          b.appendChild(goalRow);
        });
        body.appendChild(card);
      }

      if (state.perception && state.perception.length) {
        const card = _card('👁', 'Percepción del entorno', '');
        const b = card.querySelector('.glass-card-body');
        state.perception.forEach(function (p) {
          const row = _el('div', 'row-line');
          row.innerHTML = '<span>' + _esc(p.capability || '') + '</span>'
            + '<span class="dim">' + _esc(p.summary || '') + '</span>';
          b.appendChild(row);
        });
        body.appendChild(card);
      }
    }

    if (proact && !proact.error && proact.rules && proact.rules.length) {
      const card = _card('⏰', 'Proactividad', '');
      const b = card.querySelector('.glass-card-body');
      proact.rules.slice(0, 8).forEach(function (r) {
        const row = _el('div', 'row-line');
        row.innerHTML = '<span>' + _esc(r.text || r.id || r.kind || '') + '</span>'
          + '<span class="status-badge ' + (r.enabled ? 'active' : 'disabled') + '">' + (r.enabled ? 'on' : 'off') + '</span>';
        b.appendChild(row);
      });
      body.appendChild(card);
    }

    if (percep && !percep.error && percep.capabilities && percep.capabilities.length) {
      const card = _card('🛰', 'Sensores disponibles', '');
      const b = card.querySelector('.glass-card-body');
      percep.capabilities.forEach(function (c) {
        const row = _el('div', 'row-line');
        row.innerHTML = '<span>' + _esc(c.capability || '') + '</span>'
          + '<span class="status-badge ' + (c.available ? 'active' : 'disabled') + '">' + (c.available ? 'online' : 'off') + '</span>';
        b.appendChild(row);
      });
      body.appendChild(card);
    }
  }

  // --- Panel: Permisos -----------------------------------------------

  let _ownerIdCache = null;

  async function _ownerActor() {
    if (_ownerIdCache) return _ownerIdCache;
    const sec = await ILUApi.security();
    if (!sec.error && sec.owner) _ownerIdCache = sec.owner;
    return _ownerIdCache || 'owner';
  }

  /**
   * Secreto del owner para acciones administrativas. Lo pide con el
   * modal elegante (overlay + glow), jamás con prompt(); se cachea en
   * sessionStorage y no persiste entre sesiones.
   */
  function _ompin() {
    const cached = ILUApi.getPin();
    if (cached) return Promise.resolve(cached);
    if (window.ILUApp && window.ILUApp._requestPin) {
      return window.ILUApp._requestPin('Ingresá tu PIN para autorizar esta acción.').then(function (pin) {
        if (pin) ILUApi.setPin(pin);
        return pin;
      });
    }
    return Promise.resolve(null);
  }

  function _withPin(fn) {
    return _ompin().then(function (pin) {
      if (!pin) return { success: false, error: 'pin_cancelled' };
      return fn(pin);
    });
  }

  function _withPinAdminError(context, result) {
    if (result.error === 'unauthorized') {
      ILUApi.setPin('');
      _toast('No autorizado: comprobá el token de dispositivo o el PIN.', 'warn', 6000);
    } else {
      _toast((result.error || 'error') + (context ? ' (' + context + ')' : ''), 'warn');
    }
  }

  function _grantList(grant) {
    const item = _el('div', 'grant-item');
    const header = _el('div', 'grant-item-head');
    header.innerHTML = '<strong>' + _esc(grant.capability || '?') + '</strong>'
      + '<span class="dim">→ ' + _esc(grant.grantee || '?') + '</span>'
      + '<span class="status-badge active">' + (grant.expires_at ? 'exp ' + _fmtDate(grant.expires_at) : '∞') + '</span>';
    item.appendChild(header);
    if (grant.reason) {
      const reason = _el('div', 'glass-card-text dim');
      reason.textContent = grant.reason;
      item.appendChild(reason);
    }
    return item;
  }

  async function _loadPermissions(body) {
    const [sec, requests, grants] = await Promise.all([
      ILUApi.security(),
      ILUApi.authorizationRequests(),
      ILUApi.grants({ status: 'active' })
    ]);

    body.innerHTML = '';
    body.appendChild(_drawerHeader('🔐', 'Permisos', _loadPermissions));

    if (sec && !sec.error) {
      const card = _card('🛡', 'Estado de seguridad', '');
      const b = card.querySelector('.glass-card-body');
      const mode = sec.autonomy || 'manual';
      const labels = { manual: 'Manual — propongo, no ejecuto sin vos', assisted: 'Asistido — ejecuto lo seguro, pido lo demás', autonomous: 'Autónomo — actúo con autorización activa' };
      const modeLine = _el('div', 'mode-line');
      modeLine.textContent = labels[mode] || mode;
      b.appendChild(modeLine);

      const modeRow = _el('div', 'mode-buttons');
      ['manual', 'assisted', 'autonomous'].forEach(function (level) {
        const btn = _el('button', 'mode-btn' + (level === mode ? ' active' : ''));
        btn.textContent = level === 'manual' ? 'Manual' : level === 'assisted' ? 'Asistido' : 'Autónomo';
        btn.addEventListener('click', async function () {
          const actor = await _ownerActor();
          const res = await _withPin(function (pin) { return ILUApi.changeAutonomy(actor, level); });
          if (res.error) { _withPinAdminError('autonomy', res); return; }
          _toast('Modo ' + level, 'ok');
          updateModeBadge(level);
          _loadPermissions(body);
        });
        modeRow.appendChild(btn);
      });
      b.appendChild(modeRow);

      const meta = _el('div', 'glass-card-text dim');
      meta.textContent = 'Owner: ' + _esc(sec.owner || '?')
        + ' · Principals: ' + (sec.principals || 0)
        + ' · Grants activos: ' + (sec.grants_active || 0);
      b.appendChild(meta);
      body.appendChild(card);
    }

    if (requests && !requests.error) {
      const open = (requests.requests || []).filter(function (r) {
        return r.status === 'open' || r.status === 'pending';
      });

      const card = _card('❓', 'Solicitudes de autorización (' + open.length + ')', '');
      const b = card.querySelector('.glass-card-body');

      if (open.length === 0) {
        b.appendChild(_empty('Sin solicitudes abiertas'));
      } else {
        const list = _el('div', 'panel-list');
        open.forEach(function (r) {
          const reqId = r.key || r.request_id || '';
          const req = _el('div', 'auth-request-card');
          const head = _el('div', 'auth-request-head');
          head.innerHTML = '<strong>' + _esc(r.capability || '?') + '</strong>'
            + '<span class="dim">' + _fmtTime(r.created_at || r.ts || '') + '</span>';
          req.appendChild(head);
          const reason = _el('div', 'glass-card-text');
          reason.textContent = r.reason || 'Sin razón';
          req.appendChild(reason);

          const actions = _el('div', 'panel-btn-row');
          const grantOnce = _el('button', 'panel-btn small primary');
          grantOnce.textContent = 'Conceder';
          grantOnce.addEventListener('click', async function () {
            const actor = await _ownerActor();
            const res = await _withPin(function (pin) {
              return ILUApi.resolveAuthRequest(reqId, actor, 'granted', 'Concedido desde la interfaz');
            });
            if (res.error) { _withPinAdminError('auth', res); return; }
            _toast('Concedido', 'ok');
            _loadPermissions(body);
          });
          actions.appendChild(grantOnce);

          const grantRemember = _el('button', 'panel-btn small');
          grantRemember.title = 'Concede una vez y lo recuerda: I.L.U. podrá usarlo sin volver a preguntar';
          grantRemember.textContent = 'Conceder y recordar';
          grantRemember.addEventListener('click', async function () {
            const actor = await _ownerActor();
            const res = await _withPin(function (pin) {
              return ILUApi.resolveAuthRequest(reqId, actor, 'granted',
                'Concedido y recordado desde la interfaz',
                { remember: true, indefinite: true });
            });
            if (res.error) { _withPinAdminError('auth', res); return; }
            _toast('Concedido y recordado', 'ok');
            _loadPermissions(body);
          });
          actions.appendChild(grantRemember);

          const deny = _el('button', 'panel-btn small danger');
          deny.textContent = 'Denegar';
          deny.addEventListener('click', async function () {
            const actor = await _ownerActor();
            const res = await _withPin(function (pin) {
              return ILUApi.resolveAuthRequest(reqId, actor, 'denied', 'Denegado desde la interfaz');
            });
            if (res.error) { _withPinAdminError('auth', res); return; }
            _toast('Denegado', 'warn');
            _loadPermissions(body);
          });
          actions.appendChild(deny);

          req.appendChild(actions);
          list.appendChild(req);
        });
        b.appendChild(list);
      }
      body.appendChild(card);
    }

    if (grants && !grants.error) {
      const card = _card('🔑', 'Permisos otorgados', '');
      const b = card.querySelector('.glass-card-body');
      const list = (grants.grants || []).slice(0, 30);
      if (list.length === 0) {
        b.appendChild(_empty('No hay permisos concedidos'));
      } else {
        const wrap = _el('div', 'panel-list');
        list.forEach(function (g) { wrap.appendChild(_grantList(g)); });
        b.appendChild(wrap);
      }
      body.appendChild(card);
    }
  }

  // --- Panel: Sistema (diagnostics + benchmark + identidad) ----------

  async function _loadSettings(body) {
    const [about, diag] = await Promise.all([ILUApi.about(), ILUApi.diagnostics()]);
    body.innerHTML = '';
    body.appendChild(_drawerHeader('⚙', 'Sistema', _loadSettings));

    if (about && !about.error) {
      const card = _card('♡', about.name || 'I.L.U.', '');
      const b = card.querySelector('.glass-card-body');
      const desc = _el('div', 'glass-card-text');
      desc.textContent = about.description || '';
      b.appendChild(desc);
      const meta = _el('div', 'glass-card-text dim');
      meta.textContent = 'v' + (about.version || '?')
        + (about.autonomy ? ' · modo ' + _esc(about.autonomy) : '');
      b.appendChild(meta);
      body.appendChild(card);
    }

    if (diag && !diag.error) {
      const card = _card('🩺', 'Diagnóstico', '');
      const b = card.querySelector('.glass-card-body');
      (diag.checks || []).forEach(function (c) {
        const row = _el('div', 'row-line');
        const ok = c.status === 'ok' || c.ok === true;
        row.innerHTML = '<span>' + _esc(c.label || c.name || '') + '</span>'
          + '<span class="status-badge ' + (ok ? 'active' : 'danger') + '">' + (ok ? 'ok' : _esc(c.status || c.detail || 'falla')) + '</span>';
        b.appendChild(row);
      });
      body.appendChild(card);
    }

    const benchBtn = _el('button', 'panel-btn ghost');
    benchBtn.textContent = 'Correr benchmark';
    benchBtn.addEventListener('click', async function () {
      benchBtn.disabled = true;
      benchBtn.textContent = 'Corriendo…';
      const res = await _withPin(function (pin) { return ILUApi.runBenchmark('all'); });
      benchBtn.disabled = false;
      benchBtn.textContent = 'Correr benchmark';
      if (res.error) { _withPinAdminError('benchmark', res); return; }
      _toast((res.mode || 'Benchmark') + ' · score ' + Math.round((res.score || 0) * 100) + '%', 'ok');
      const ran = _el('div', 'bench-row');
      ran.innerHTML = '<span>Benchmark ' + _esc(res.mode || '') + '</span>'
        + '<span class="dim">' + (res.executed || 0) + ' casos · score ' + Math.round((res.score || 0) * 100) + '%</span>';
      body.appendChild(ran);
      _toast('Benchmark terminado', 'ok');
    });
    body.appendChild(benchBtn);
  }

  // --- API pública ----------------------------------------------------

  return {
    // Drawer
    openDrawer: openDrawer,
    closeDrawer: closeDrawer,
    // Chat
    appendMessage: appendMessage,
    showTypingIndicator: showTypingIndicator,
    removeTypingIndicator: removeTypingIndicator,
    liveUserMessage: liveUserMessage,
    clearLiveUserMessage: clearLiveUserMessage,
    updateMessage: updateMessage,
    setMessageMeta: setMessageMeta,
    // Mini-status
    updateSidebarContext: updateSidebarContext,
    updateSidebarTool: updateSidebarTool,
    updateSidebarSubagent: updateSidebarSubagent,
    updateSidebarProvider: updateSidebarProvider,
    updateModeBadge: updateModeBadge,
    // Toasts
    toast: _toast,
    // Escapado util
    _esc: _esc
  };
})();