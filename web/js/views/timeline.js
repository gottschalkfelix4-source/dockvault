import { api } from '../api.js';
import { html, raw, bytes, date, ago, duration, esc, on, statusLabel } from '../util.js';
import { modal, toast } from '../components.js';
import { openRestoreWizard } from './restore.js';

const RANGES = [
  { days: 1, label: '24 Std', ticks: 8, fmt: (d) => d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' }) },
  { days: 7, label: '7 Tage', ticks: 7, fmt: (d) => d.toLocaleDateString('de-DE', { weekday: 'short', day: '2-digit' }) },
  { days: 30, label: '30 Tage', ticks: 6, fmt: (d) => d.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit' }) },
  { days: 90, label: '90 Tage', ticks: 6, fmt: (d) => d.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit' }) },
  { days: 365, label: '1 Jahr', ticks: 6, fmt: (d) => d.toLocaleDateString('de-DE', { month: 'short', year: '2-digit' }) },
];

let activeRange = 2;
let filterContainer = '';

export default {
  title: 'Zeitstrahl',
  subtitle: 'Jede Sicherung, jede Wiederherstellung, jedes Ereignis in zeitlicher Abfolge',

  async render(root, { actions, rerender }) {
    const range = RANGES[activeRange];
    const [data, containerData] = await Promise.all([
      api.timeline(range.days, filterContainer || undefined),
      api.containers().catch(() => ({ containers: [] })),
    ]);

    actions.innerHTML = `<button class="btn" data-act="reload">Aktualisieren</button>`;

    const start = new Date(data.since).getTime();
    const end = Date.now();
    const span = Math.max(end - start, 1);
    const pct = (iso) => Math.min(99.6, Math.max(0.4, ((new Date(iso).getTime() - start) / span) * 100));

    root.innerHTML = html`
      <div class="timeline-controls">
        <div class="seg">
          ${RANGES.map((r, i) => html`
            <button data-range="${String(i)}" class="${i === activeRange ? 'active' : ''}">${r.label}</button>`)}
        </div>
        <select data-filter style="width:auto;min-width:200px">
          <option value="">Alle Container</option>
          ${containerData.containers.map((c) => html`
            <option value="${c.name}" ${c.name === filterContainer ? raw('selected') : ''}>${c.name}</option>`)}
        </select>
        <span class="muted" style="font-size:12.5px">
          ${String(data.entries.length)} Ereignisse seit ${date(data.since)}</span>
      </div>

      ${data.lanes.length ? raw(html`
        <div class="tl">
          <div class="tl-axis">${raw(axisTicks(range, start, span))}</div>
          ${data.lanes.map((lane) => html`
            <div class="tl-lane">
              <div class="tl-lane-head">
                <span class="n" title="${lane.container}">${lane.container}</span>
                <span class="m">${String(lane.backups)} Backups · ${bytes(lane.bytes)}</span>
              </div>
              <div class="tl-track">
                ${lane.entries.map((entry) => dot(entry, pct(entry.ts)))}
              </div>
            </div>`)}
        </div>
        <div class="tl-legend">
          <span><i style="background:var(--ok)"></i>Backup erfolgreich</span>
          <span><i style="background:var(--warn)"></i>Backup mit Warnungen</span>
          <span><i style="background:var(--err)"></i>Fehlgeschlagen</span>
          <span><i style="background:var(--info);border-radius:3px"></i>Wiederherstellung</span>
          <span><i style="background:var(--text-3);border-radius:2px"></i>Sonstiges Ereignis</span>
          <span style="margin-left:auto">Klick auf einen Punkt öffnet die Details</span>
        </div>`) : raw(`<div class="empty"><strong>Noch keine Ereignisse in diesem Zeitraum</strong>
          Wähle einen größeren Zeitraum oder starte ein erstes Backup.</div>`)}

      <div class="card" style="margin-top:20px">
        <div class="card-head"><h2>Chronologie</h2>
          <span class="muted" style="font-size:12.5px">neueste zuerst</span></div>
        <div class="feed">
          ${data.entries.slice(0, 80).map(feedItem)}
        </div>
        ${data.entries.length > 80 ? raw(`<p class="muted" style="margin:12px 0 0;font-size:12.5px">
          … und ${data.entries.length - 80} weitere Ereignisse.</p>`) : ''}
      </div>`;

    // --- Interaktion ---------------------------------------------------
    on(root, '[data-range]', (button) => {
      activeRange = Number(button.dataset.range);
      rerender();
    });

    root.querySelector('[data-filter]')?.addEventListener('change', (event) => {
      filterContainer = event.target.value;
      rerender();
    });

    on(actions, '[data-act="reload"]', () => rerender());

    let tooltip = null;
    on(root, '.tl-dot', (node) => showEntryDetail(JSON.parse(node.dataset.entry)));
    root.addEventListener('mouseover', (event) => {
      const node = event.target.closest('.tl-dot');
      if (!node) return;
      const entry = JSON.parse(node.dataset.entry);
      tooltip = document.createElement('div');
      tooltip.className = 'tl-tooltip';
      tooltip.innerHTML = html`
        <b>${entry.label || entry.type}</b>
        <div class="k">${date(entry.ts, true)}</div>
        ${entry.type === 'backup' ? raw(html`
          <div class="k">${bytes(entry.bytes)} · ${duration(entry.duration_s)}
            · ${statusLabel(entry.status)}</div>`) : ''}`;
      document.body.appendChild(tooltip);
      const rect = node.getBoundingClientRect();
      tooltip.style.left = Math.min(window.innerWidth - 320, rect.left) + 'px';
      tooltip.style.top = (rect.top - tooltip.offsetHeight - 8) + 'px';
    });
    root.addEventListener('mouseout', (event) => {
      if (event.target.closest('.tl-dot') && tooltip) { tooltip.remove(); tooltip = null; }
    });
  },
};

function axisTicks(range, start, span) {
  const out = [];
  for (let i = 0; i <= range.ticks; i++) {
    const position = (i / range.ticks) * 100;
    const moment = new Date(start + (span * i) / range.ticks);
    out.push(`<div class="tl-tick" style="left:${position}%"><span>${esc(range.fmt(moment))}</span></div>`);
  }
  return out.join('');
}

function dot(entry, position) {
  const payload = esc(JSON.stringify(entry));
  if (entry.type === 'backup') {
    const cls = { completed: '', partial: 'partial', failed: 'failed', running: 'running' }[entry.status] ?? '';
    return `<span class="tl-dot ${cls} ${entry.pinned ? 'pinned' : ''}"
              style="left:${position}%" data-entry="${payload}"></span>`;
  }
  const kind = entry.type.startsWith('restore') || entry.type.startsWith('template') ? 'restore'
             : entry.status === 'error' ? 'error'
             : entry.status === 'warn' ? 'warn' : '';
  return `<span class="tl-dot event ${kind}" style="left:${position}%" data-entry="${payload}"></span>`;
}

function feedItem(entry) {
  const mark = entry.type === 'backup'
    ? ({ completed: 'ok', partial: 'warn', failed: 'err' }[entry.status] || '')
    : (entry.type.startsWith('restore') || entry.type.startsWith('template') ? 'info'
       : entry.status === 'error' ? 'err' : entry.status === 'warn' ? 'warn' : '');

  const meta = entry.type === 'backup'
    ? `${bytes(entry.bytes)} · ${duration(entry.duration_s)} · ${entry.trigger === 'schedule'
        ? 'per Zeitplan' : 'manuell'}${entry.has_template ? ' · mit Template' : ''}`
    : (entry.container ? entry.container : 'System');

  return html`
    <div class="feed-item">
      <div class="feed-time">${date(entry.ts, true)}</div>
      <div class="feed-mark ${mark}"></div>
      <div class="feed-body">
        ${entry.label}
        <div class="meta">${meta}</div>
      </div>
    </div>`;
}

async function showEntryDetail(entry) {
  if (entry.type !== 'backup') {
    modal({
      title: entry.label,
      subtitle: date(entry.ts, true),
      body: html`<table>
        <tr><th>Typ</th><td class="mono">${entry.type}</td></tr>
        <tr><th>Container</th><td>${entry.container || '—'}</td></tr>
        ${entry.job_id ? raw(html`<tr><th>Auftrag</th><td class="mono">
          <a href="#/jobs/${entry.job_id}">${entry.job_id}</a></td></tr>`) : ''}
      </table>`,
      footer: '<button class="btn" data-close>Schließen</button>',
    });
    return;
  }

  let record;
  try {
    record = await api.backup(entry.backup_ref);
  } catch (error) {
    toast(error.message, 'err');
    return;
  }
  const manifest = record.manifest || {};
  const artifacts = manifest.artifacts || [];

  const dialog = modal({
    title: `Backup · ${record.container}`,
    subtitle: date(record.created_at, true),
    wide: true,
    body: html`
      <div class="grid cols-3">
        <div class="stat"><div class="label">Größe</div>
          <div class="value" style="font-size:19px">${bytes(record.archive_bytes)}</div>
          <div class="sub">aus ${bytes(record.source_bytes)} Rohdaten</div></div>
        <div class="stat"><div class="label">Dauer</div>
          <div class="value" style="font-size:19px">${duration(record.duration_s)}</div>
          <div class="sub">${record.trigger}</div></div>
        <div class="stat"><div class="label">Template</div>
          <div class="value" style="font-size:19px">${record.has_template ? 'Ja' : 'Nein'}</div>
          <div class="sub">${(manifest.template || {}).source || '—'}</div></div>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Quelle</th><th>Im Container</th><th>Roh</th><th>Archiv</th><th>Dateien</th></tr></thead>
          <tbody>
            ${artifacts.map((a) => html`
              <tr>
                <td><span class="badge">${a.kind}</span> ${a.name}</td>
                <td class="mono">${a.destination}</td>
                <td class="mono nowrap">${a.skipped ? '—' : bytes(a.source_bytes)}</td>
                <td class="mono nowrap">${a.skipped ? raw(`<span class="badge warn">übersprungen</span>`)
                                                    : bytes(a.archive_bytes)}</td>
                <td class="mono">${a.files ?? '—'}</td>
              </tr>`)}
          </tbody>
        </table>
      </div>
      ${(manifest.failures || []).length ? raw(html`
        <div class="notice err"><strong>Fehler:</strong>
          <ul>${manifest.failures.map((f) => html`<li>${f}</li>`)}</ul></div>`) : ''}`,
    footer: `<button class="btn" data-close>Schließen</button>
             <button class="btn primary" data-restore>Aus diesem Stand wiederherstellen</button>`,
  });

  dialog.root.querySelector('[data-restore]').addEventListener('click', () => {
    dialog.close();
    openRestoreWizard(record.id);
  });
}
