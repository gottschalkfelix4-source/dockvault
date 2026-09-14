// Router, globaler Zustand und Live-Anbindung.

import { api, connectStream, subscribe } from './api.js';
import { el, els, html, raw } from './util.js';
import { toast } from './components.js';

import dashboard from './views/dashboard.js';
import timeline from './views/timeline.js';
import containers from './views/containers.js';
import backups from './views/backups.js';
import restore from './views/restore.js';
import schedules from './views/schedules.js';
import jobs from './views/jobs.js';
import settings from './views/settings.js';

const routes = { dashboard, timeline, containers, backups, restore, schedules, jobs, settings };

export const state = { status: null, route: 'dashboard', params: {} };

const view = () => el('#view');
const actions = () => el('#topbar-actions');

/**
 * Ersetzt einen Container durch ein frisches Element gleicher Identität.
 * Ansichten hängen delegierte Listener an diese Container; ohne den Austausch
 * würden sie sich bei jedem Rendern stapeln und Handler mehrfach feuern.
 */
function freshen(node, tag, className) {
  const replacement = document.createElement(tag);
  replacement.id = node.id;
  if (className) replacement.className = className;
  node.replaceWith(replacement);
  return replacement;
}

// ---------------------------------------------------------------- Router

function parseHash() {
  const hash = location.hash.replace(/^#\/?/, '') || 'dashboard';
  const [name, ...rest] = hash.split('/');
  return { name: routes[name] ? name : 'dashboard', params: { arg: rest.join('/') } };
}

async function render() {
  const { name, params } = parseHash();
  state.route = name;
  state.params = params;

  els('.nav a').forEach((link) => link.classList.toggle('active', link.dataset.route === name));

  const module = routes[name];
  el('#page-title').textContent = module.title;
  el('#page-subtitle').textContent = module.subtitle || '';

  const actionBar = freshen(actions(), 'div', 'topbar-actions');
  const viewBox = freshen(view(), 'section', 'view');
  viewBox.innerHTML = '<div class="loading">Lade …</div>';

  try {
    await module.render(viewBox, { actions: actionBar, params, navigate,
                                   refreshStatus, rerender: render });
  } catch (error) {
    viewBox.innerHTML = html`
      <div class="notice err"><strong>Fehler beim Laden:</strong> ${error.message}</div>`;
    console.error(error);
  }
}

export function navigate(path) {
  if (location.hash === '#/' + path) render();
  else location.hash = '#/' + path;
}

// ---------------------------------------------------------------- Statuszeile

function setStatusLine(id, ok, text) {
  const node = el('#' + id);
  node.className = 'statusline ' + (ok === true ? 'ok' : ok === false ? 'err' : 'warn');
  node.querySelector('span').textContent = text;
}

export async function refreshStatus() {
  try {
    const status = await api.status();
    state.status = status;
    el('#brand-version').textContent = 'v' + status.version;
    setStatusLine('status-docker', status.docker.available,
      status.docker.available ? `Docker ${status.docker.docker_version || ''}`.trim()
                              : 'Docker nicht erreichbar');
    setStatusLine('status-unraid', status.unraid.templates_available,
      status.unraid.templates_available
        ? `${status.unraid.template_count} Unraid-Templates`
        : 'Templates nicht gemountet');
    return status;
  } catch (error) {
    setStatusLine('status-docker', false, 'Backend nicht erreichbar');
    throw error;
  }
}

// ---------------------------------------------------------------- Laufende Aufträge

const activeJobs = new Map();

function renderActiveJobs() {
  const container = el('#active-jobs');
  if (!activeJobs.size) { container.hidden = true; container.innerHTML = ''; return; }
  container.hidden = false;
  container.innerHTML = [...activeJobs.values()].map((job) => html`
    <div class="job-strip">
      <div class="spread">
        <span><strong>${jobTypeLabel(job.type)}</strong> · ${job.target || ''}</span>
        <span class="muted mono">${Math.round(job.progress || 0)} %</span>
      </div>
      <div class="progress"><i style="width:${String(job.progress || 0)}%"></i></div>
      <div class="muted" style="font-size:12px">${job.message || ''}</div>
    </div>`).join('');
}

function jobTypeLabel(type) {
  return { backup: 'Backup', 'backup-bulk': 'Sammel-Backup', restore: 'Wiederherstellung',
           schedule: 'Zeitplan' }[type] || type;
}

// ---------------------------------------------------------------- Start

function wireLiveEvents() {
  connectStream((connectionState) => {
    setStatusLine('status-stream', connectionState === 'connected',
      connectionState === 'connected' ? 'Live verbunden' : 'Verbindung wird aufgebaut …');
  });

  subscribe('job.started', (data) => {
    activeJobs.set(data.job_id, { ...data, progress: 0, message: 'Startet …' });
    renderActiveJobs();
  });

  subscribe('job.progress', (data) => {
    activeJobs.set(data.job_id, { ...activeJobs.get(data.job_id), ...data });
    renderActiveJobs();
  });

  subscribe('job.finished', (data) => {
    activeJobs.delete(data.job_id);
    renderActiveJobs();
    const label = `${jobTypeLabel(data.type)} · ${data.target || ''}`;
    if (data.status === 'completed') toast(`${label}: abgeschlossen`, 'ok');
    else if (data.status === 'failed') toast(`${label}: fehlgeschlagen — ${data.error || ''}`, 'err', 8000);
    else toast(`${label}: ${data.status}`, 'warn');

    refreshStatus().catch(() => {});
    if (['dashboard', 'timeline', 'backups', 'containers', 'jobs', 'restore'].includes(state.route)) {
      render();
    }
  });

  subscribe('container.changed', () => {
    if (state.route === 'containers' || state.route === 'dashboard') render();
  });
}

async function boot() {
  wireLiveEvents();
  try {
    await refreshStatus();
  } catch {
    view().innerHTML = html`<div class="notice err">
      Das Backend antwortet nicht. Läuft der DockVault-Container?</div>`;
    return;
  }
  window.addEventListener('hashchange', render);
  await render();
  setInterval(() => refreshStatus().catch(() => {}), 30000);
}

boot();
