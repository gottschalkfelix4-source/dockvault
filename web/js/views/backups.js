import { api } from '../api.js';
import { html, raw, bytes, date, duration, on, statusClass, statusLabel, shortImage }
  from '../util.js';
import { toast, confirm, modal } from '../components.js';
import { openRestoreWizard } from './restore.js';

export default {
  title: 'Backups',
  subtitle: 'Alle gespeicherten Stände, Größen und Aufbewahrung',

  async render(root, { actions, params, rerender }) {
    const container = params.arg || '';
    const { backups } = await api.backups(container || undefined);
    const { containers } = await api.containers().catch(() => ({ containers: [] }));

    actions.innerHTML = `
      <button class="btn" data-act="rescan">Index neu einlesen</button>`;

    const total = backups.reduce((sum, b) => sum + (b.archive_bytes || 0), 0);
    const grouped = new Map();
    for (const record of backups) {
      if (!grouped.has(record.container)) grouped.set(record.container, []);
      grouped.get(record.container).push(record);
    }

    root.innerHTML = html`
      <div class="timeline-controls">
        <select data-container style="width:auto;min-width:220px">
          <option value="">Alle Container</option>
          ${containers.map((c) => html`
            <option value="${c.name}" ${c.name === container ? raw('selected') : ''}>${c.name}</option>`)}
        </select>
        <span class="muted" style="font-size:12.5px">
          ${String(backups.length)} Sicherungen · ${bytes(total)} gesamt</span>
      </div>

      ${grouped.size ? raw([...grouped.entries()].map(([name, records]) => html`
        <div class="card" style="margin-bottom:14px">
          <div class="card-head">
            <div>
              <h2>${name}</h2>
              <p class="muted" style="margin:2px 0 0;font-size:12.5px">
                ${String(records.length)} Stände ·
                ${bytes(records.reduce((s, r) => s + (r.archive_bytes || 0), 0))}</p>
            </div>
            <button class="btn sm" data-act="backup-now" data-name="${name}">Jetzt sichern</button>
          </div>
          <div class="table-wrap" style="border:0">
            <table>
              <thead><tr><th>Zeitpunkt</th><th>Auslöser</th><th>Roh</th><th>Archiv</th>
                <th>Dauer</th><th>Template</th><th>Status</th><th></th></tr></thead>
              <tbody>
                ${records.map((record) => html`
                  <tr>
                    <td class="nowrap">${date(record.created_at, true)}</td>
                    <td class="muted">${record.trigger}</td>
                    <td class="mono nowrap">${bytes(record.source_bytes)}</td>
                    <td class="mono nowrap">${bytes(record.archive_bytes)}</td>
                    <td class="muted nowrap">${duration(record.duration_s)}</td>
                    <td>${record.has_template ? raw('<span class="badge ok">ja</span>')
                                              : raw('<span class="badge">nein</span>')}</td>
                    <td><span class="badge ${statusClass(record.status)}">
                      ${statusLabel(record.status)}</span>
                      ${record.pinned ? raw('<span class="badge warn">📌</span>') : ''}</td>
                    <td class="nowrap">
                      <button class="btn sm" data-act="restore" data-ref="${record.id}">Restore</button>
                      <button class="btn ghost sm" data-act="detail" data-ref="${record.id}">Details</button>
                      <button class="btn ghost sm" data-act="pin" data-ref="${record.id}"
                        data-pinned="${String(!!record.pinned)}"
                        title="Angeheftete Backups werden von der Aufbewahrung nicht gelöscht">
                        ${record.pinned ? 'Lösen' : 'Anheften'}</button>
                      <button class="btn ghost sm" data-act="delete" data-ref="${record.id}">✕</button>
                    </td>
                  </tr>`)}
              </tbody>
            </table>
          </div>
        </div>`).join('')) : raw(`<div class="empty"><strong>Keine Sicherungen</strong>
          Unter „Container" lässt sich die erste anlegen.</div>`)}`;

    root.querySelector('[data-container]')?.addEventListener('change', (event) => {
      location.hash = '#/backups' + (event.target.value ? '/' + event.target.value : '');
    });

    on(root, '[data-act="restore"]', (button) => openRestoreWizard(button.dataset.ref));
    on(root, '[data-act="detail"]', (button) => showDetail(button.dataset.ref));

    on(root, '[data-act="backup-now"]', async (button) => {
      try {
        await api.startBackup(button.dataset.name);
        toast(`Backup von ${button.dataset.name} gestartet`, 'ok');
      } catch (error) { toast(error.message, 'err'); }
    });

    on(root, '[data-act="pin"]', async (button) => {
      const pinned = button.dataset.pinned !== 'true';
      try {
        await api.pinBackup(button.dataset.ref, pinned);
        toast(pinned ? 'Backup angeheftet — wird nicht automatisch gelöscht'
                     : 'Anheftung gelöst', 'ok');
        rerender();
      } catch (error) { toast(error.message, 'err'); }
    });

    on(root, '[data-act="delete"]', async (button) => {
      const ok = await confirm({
        title: 'Backup löschen',
        message: `Der Stand ${button.dataset.ref} wird unwiderruflich vom Datenträger entfernt.`,
        confirmLabel: 'Löschen', danger: true,
      });
      if (!ok) return;
      try {
        await api.deleteBackup(button.dataset.ref);
        toast('Backup gelöscht', 'ok');
        rerender();
      } catch (error) { toast(error.message, 'err'); }
    });

    on(actions, '[data-act="rescan"]', async () => {
      const result = await api.rescan();
      toast(`${result.found} Ordner geprüft, ${result.added} neu indexiert`, 'ok');
      rerender();
    });
  },
};

async function showDetail(ref) {
  const record = await api.backup(ref);
  const manifest = record.manifest || {};
  const dialog = modal({
    title: `Backup · ${record.container}`,
    subtitle: `${ref} — ${date(record.created_at, true)}`,
    wide: true,
    body: html`
      <div class="grid cols-4">
        <div class="stat"><div class="label">Archiv</div>
          <div class="value" style="font-size:18px">${bytes(record.archive_bytes)}</div></div>
        <div class="stat"><div class="label">Rohdaten</div>
          <div class="value" style="font-size:18px">${bytes(record.source_bytes)}</div></div>
        <div class="stat"><div class="label">Dauer</div>
          <div class="value" style="font-size:18px">${duration(record.duration_s)}</div></div>
        <div class="stat"><div class="label">Kompression</div>
          <div class="value" style="font-size:18px">${manifest.compression || '—'}</div></div>
      </div>

      <h3>Inhalt</h3>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Art</th><th>Name</th><th>Im Container</th><th>Archivdatei</th>
            <th>Größe</th><th>SHA256</th></tr></thead>
          <tbody>
            ${(manifest.artifacts || []).map((a) => html`
              <tr>
                <td><span class="badge">${a.kind}</span></td>
                <td>${a.name}</td>
                <td class="mono">${a.destination}</td>
                <td class="mono">${a.file || '—'}</td>
                <td class="mono nowrap">${a.skipped ? raw(`<span class="badge warn">${a.reason || 'übersprungen'}</span>`)
                                                    : bytes(a.archive_bytes)}</td>
                <td class="mono">${a.sha256 ? a.sha256.slice(0, 12) : '—'}</td>
              </tr>`)}
          </tbody>
        </table>
      </div>

      <h3>Container-Konfiguration</h3>
      <table>
        <tr><th>Image</th><td class="mono">${(manifest.container || {}).image || record.image}</td></tr>
        <tr><th>Netzwerke</th><td>${(manifest.networks || []).map((n) => n.name).join(', ') || '—'}</td></tr>
        <tr><th>Template</th><td>${(manifest.template || {}).file || '—'}
          <span class="badge">${(manifest.template || {}).source || '—'}</span></td></tr>
        <tr><th>Ablageort</th><td class="mono">${record.path}</td></tr>
      </table>`,
    footer: `<button class="btn" data-close>Schließen</button>
             ${record.has_template ? '<button class="btn" data-template>Template ansehen</button>' : ''}
             <button class="btn primary" data-restore>Wiederherstellen</button>`,
  });

  dialog.root.querySelector('[data-restore]').addEventListener('click', () => {
    dialog.close();
    openRestoreWizard(ref);
  });
  dialog.root.querySelector('[data-template]')?.addEventListener('click', async () => {
    const xml = await api.backupTemplate(ref);
    modal({
      title: 'Gesichertes Unraid-Template',
      wide: true,
      body: html`<textarea readonly style="min-height:400px">${xml}</textarea>`,
      footer: '<button class="btn" data-close>Schließen</button>',
    });
  });
}
