import { api } from '../api.js';
import { html, raw, date, duration, on, statusClass, esc } from '../util.js';
import { modal, toast } from '../components.js';

const TYPE_LABEL = { backup: 'Backup', 'backup-bulk': 'Sammel-Backup',
                     restore: 'Wiederherstellung', schedule: 'Zeitplan' };

export default {
  title: 'Aufträge',
  subtitle: 'Verlauf aller Backup- und Restore-Läufe mit vollständigem Protokoll',

  async render(root, { actions, params, rerender }) {
    const { jobs, active } = await api.jobs();
    actions.innerHTML = `<button class="btn" data-act="reload">Aktualisieren</button>`;

    root.innerHTML = html`
      ${active.length ? raw(html`
        <div class="card" style="margin-bottom:16px;border-color:var(--accent)">
          <div class="card-head"><h2>Läuft gerade</h2>
            <span class="badge info">${String(active.length)}</span></div>
          ${active.map((job) => html`
            <div class="spread" style="padding:8px 0">
              <div>
                <strong>${TYPE_LABEL[job.type] || job.type}</strong> · ${job.target || ''}
                <div class="muted" style="font-size:12px">${job.message || ''}</div>
              </div>
              <div class="row">
                <span class="mono">${String(Math.round(job.progress))} %</span>
                <button class="btn ghost sm" data-act="cancel" data-id="${job.job_id}">Abbrechen</button>
                <button class="btn sm" data-act="open" data-id="${job.job_id}">Protokoll</button>
              </div>
            </div>`)}
        </div>`) : ''}

      <div class="table-wrap">
        <table>
          <thead><tr><th>Art</th><th>Ziel</th><th>Start</th><th>Dauer</th>
            <th>Status</th><th>Meldung</th><th></th></tr></thead>
          <tbody>
            ${jobs.map((job) => html`
              <tr>
                <td>${TYPE_LABEL[job.type] || job.type}</td>
                <td><strong>${job.target || '—'}</strong></td>
                <td class="nowrap">${date(job.started_at, true)}</td>
                <td class="muted nowrap">${job.finished_at
                  ? duration((new Date(job.finished_at) - new Date(job.started_at)) / 1000) : '—'}</td>
                <td><span class="badge ${statusClass(job.status)}">${job.status}</span></td>
                <td class="muted" style="max-width:280px;overflow:hidden;text-overflow:ellipsis">
                  ${job.error || job.message || ''}</td>
                <td><button class="btn sm" data-act="open" data-id="${job.id}">Protokoll</button></td>
              </tr>`)}
            ${jobs.length ? '' : raw('<tr><td colspan="7" class="muted">Noch keine Aufträge.</td></tr>')}
          </tbody>
        </table>
      </div>`;

    on(root, '[data-act="open"]', (button) => showJob(button.dataset.id));
    on(actions, '[data-act="reload"]', () => rerender());
    on(root, '[data-act="cancel"]', async (button) => {
      try {
        await api.cancelJob(button.dataset.id);
        toast('Abbruch angefordert', 'warn');
      } catch (error) { toast(error.message, 'err'); }
    });

    if (params.arg) showJob(params.arg);
  },
};

async function showJob(jobId) {
  let job;
  try { job = await api.job(jobId); } catch (error) { toast(error.message, 'err'); return; }

  modal({
    title: `${TYPE_LABEL[job.type] || job.type} · ${job.target || ''}`,
    subtitle: `${jobId} — gestartet ${date(job.started_at, true)}`,
    wide: true,
    body: html`
      <div class="progress ${job.status === 'completed' ? 'ok' : job.status === 'failed' ? 'err' : ''}">
        <i style="width:${String(job.progress || 0)}%"></i></div>
      <div class="spread">
        <span class="badge ${statusClass(job.status)}">${job.status}</span>
        <span class="muted" style="font-size:12.5px">${job.message || ''}</span>
      </div>
      ${job.error ? raw(html`<div class="notice err">${job.error}</div>`) : ''}
      <div class="logbox">${raw((job.log || []).map((line) =>
        `<div class="${line.level !== 'info' ? 'l-' + line.level : ''}">` +
        `<span class="l-ts">${esc(new Date(line.ts).toLocaleTimeString('de-DE'))}</span>  ` +
        `${esc(line.line)}</div>`).join('') || '<em>Kein Protokoll</em>')}</div>`,
    footer: '<button class="btn" data-close>Schließen</button>',
  });
}
