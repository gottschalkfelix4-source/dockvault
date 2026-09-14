// REST-Client und Live-Verbindung (SSE).

async function request(path, options = {}) {
  const response = await fetch('/api' + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const isJson = (response.headers.get('content-type') || '').includes('application/json');
  const payload = isJson ? await response.json().catch(() => null) : await response.text();
  if (!response.ok) {
    const message = (payload && payload.detail) || payload || `HTTP ${response.status}`;
    throw new Error(typeof message === 'string' ? message : JSON.stringify(message));
  }
  return payload;
}

const get = (path) => request(path);
const post = (path, body) => request(path, { method: 'POST', body });
const put = (path, body) => request(path, { method: 'PUT', body });
const del = (path) => request(path, { method: 'DELETE' });

export const api = {
  status: () => get('/status'),
  settings: () => get('/settings'),
  saveSettings: (patch) => put('/settings', patch),

  storage: () => get('/storage'),
  testStorage: (params) => post('/storage/test', params),
  mountStorage: () => post('/storage/mount'),
  unmountStorage: () => post('/storage/unmount'),

  detectRoots: () => get('/backup-roots/detect'),

  containers: () => get('/containers'),
  container: (name) => get(`/containers/${encodeURIComponent(name)}`),
  containerLogs: (name, tail = 300) =>
    get(`/containers/${encodeURIComponent(name)}/logs?tail=${tail}`),
  containerAction: (name, action) =>
    post(`/containers/${encodeURIComponent(name)}/action`, { action }),
  backupPlan: (name) => get(`/containers/${encodeURIComponent(name)}/plan`),

  backups: (container) => get('/backups' + (container ? `?container=${encodeURIComponent(container)}` : '')),
  backup: (ref) => get(`/backups/${ref}`),
  startBackup: (container, options) => post('/backups', { container, options }),
  startBulkBackup: (body) => post('/backups/bulk', body),
  deleteBackup: (ref) => del(`/backups/${ref}`),
  pinBackup: (ref, pinned) => post(`/backups/${ref}/pin?pinned=${pinned}`),
  browseBackup: (ref, artifact) =>
    get(`/backups/${ref}/browse?artifact=${encodeURIComponent(artifact)}`),
  backupTemplate: (ref) => get(`/backups/${ref}/template`),
  rescan: () => post('/backups/rescan'),

  restorePreview: (body) => post('/restore/preview', body),
  restore: (body) => post('/restore', body),

  templates: () => get('/templates'),
  orphans: () => get('/templates/orphans'),
  templateFile: (filename) => get(`/templates/file/${encodeURIComponent(filename)}`),
  generateTemplate: (name, write = false) =>
    post(`/templates/generate/${encodeURIComponent(name)}?write=${write}`),

  timeline: (days = 30, container) =>
    get(`/timeline?days=${days}` + (container ? `&container=${encodeURIComponent(container)}` : '')),

  jobs: () => get('/jobs'),
  job: (id) => get(`/jobs/${id}`),
  cancelJob: (id) => post(`/jobs/${id}/cancel`),

  schedules: () => get('/schedules'),
  createSchedule: (body) => post('/schedules', body),
  updateSchedule: (id, body) => put(`/schedules/${id}`, body),
  deleteSchedule: (id) => del(`/schedules/${id}`),
  runSchedule: (id) => post(`/schedules/${id}/run`),
};

// ---------------------------------------------------------------- Live-Stream

const listeners = new Map();
let source = null;
let onStateChange = () => {};

export function subscribe(type, handler) {
  if (!listeners.has(type)) listeners.set(type, new Set());
  listeners.get(type).add(handler);
  return () => listeners.get(type).delete(handler);
}

function emit(type, data) {
  (listeners.get(type) || []).forEach((fn) => fn(data));
  (listeners.get('*') || []).forEach((fn) => fn({ type, data }));
}

export function connectStream(stateHandler) {
  if (stateHandler) onStateChange = stateHandler;
  if (source) source.close();

  source = new EventSource('/api/events/stream');
  source.onopen = () => onStateChange('connected');
  source.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data);
      emit(payload.type, payload.data);
    } catch { /* Keep-Alive-Zeilen ignorieren */ }
  };
  source.onerror = () => {
    onStateChange('reconnecting');
    // EventSource verbindet selbstständig neu; nur den Status melden.
  };
  return source;
}
