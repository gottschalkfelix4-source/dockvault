import { api } from '../api.js';
import { html, raw, bytes, ago, date, duration, statusClass, statusLabel, on, shortImage }
  from '../util.js';
import { toast, confirm } from '../components.js';
import { openRestoreWizard } from './restore.js';

export default {
  title: 'Übersicht',
  subtitle: 'Schutzstatus aller Container auf einen Blick',

  async render(root, { actions, navigate }) {
    const [status, backupData, orphanData] = await Promise.all([
      api.status(), api.backups(), api.orphans(),
    ]);

    actions.innerHTML = `
      <button class="btn" data-act="rescan">Index prüfen</button>
      <button class="btn primary" data-act="backup-all">Alles sichern</button>`;

    const recent = backupData.backups.slice(0, 12);
    const failed = backupData.backups.filter((b) => b.status === 'failed').length;
    const orphanCount = orphanData.orphan_backups.length + orphanData.orphan_templates.length;

    root.innerHTML = html`
      <div class="grid cols-4" style="margin-bottom:18px">
        <div class="stat">
          <div class="label">Container</div>
          <div class="value">${status.counts.containers}</div>
          <div class="sub">${status.counts.running} laufen</div>
        </div>
        <div class="stat ${status.counts.unprotected ? 'warn' : 'ok'}">
          <div class="label">Gesichert</div>
          <div class="value">${status.counts.protected} / ${status.counts.containers}</div>
          <div class="sub">${status.counts.unprotected} ohne Sicherung</div>
        </div>
        <div class="stat">
          <div class="label">Backups</div>
          <div class="value">${status.counts.backups}</div>
          <div class="sub">${bytes(status.total_archive_bytes)} belegt</div>
        </div>
        <div class="stat ${failed ? 'err' : ''}">
          <div class="label">Speicherplatz frei</div>
          <div class="value">${bytes(status.storage.free)}</div>
          <div class="sub">${failed ? failed + ' fehlgeschlagene Backups' : 'keine Fehler'}</div>
        </div>
      </div>

      ${raw(healthNotices(status))}

      ${orphanCount ? raw(html`
        <div class="card" style="margin-bottom:16px;border-color:var(--info)">
          <div class="card-head">
            <div>
              <h2>Wiederherstellbar</h2>
              <p class="muted" style="margin:2px 0 0;font-size:12.5px">
                Diese Container existieren nicht mehr, lassen sich aber zurückholen.</p>
            </div>
            <span class="badge info">${orphanCount}</span>
          </div>
          <div class="grid cols-2">
            ${orphanData.orphan_backups.slice(0, 6).map((entry) => html`
              <div class="ctr-card">
                <div class="ctr-head">
                  <div class="ctr-icon">↺</div>
                  <div style="min-width:0">
                    <div class="ctr-name">${entry.container}</div>
                    <div class="ctr-img">${shortImage(entry.image)}</div>
                  </div>
                </div>
                <div class="ctr-meta">
                  <span class="badge">Backup ${ago(entry.created_at)}</span>
                  <span class="badge">${bytes(entry.archive_bytes)}</span>
                  ${entry.has_template ? raw('<span class="badge ok">Template dabei</span>')
                                       : raw('<span class="badge warn">Template wird erzeugt</span>')}
                </div>
                <div class="ctr-actions">
                  <button class="btn primary sm" data-act="restore" data-ref="${entry.backup_ref}">
                    Wiederherstellen</button>
                </div>
              </div>`)}
          </div>
          ${orphanData.orphan_templates.length ? raw(html`
            <p class="muted" style="margin:12px 0 0;font-size:12.5px">
              Zusätzlich liegen ${String(orphanData.orphan_templates.length)} Unraid-Template(s)
              ohne Container vor —
              <a href="#/restore">unter „Wiederherstellen" ansehen</a>.</p>`) : ''}
        </div>`) : ''}

      <div class="grid cols-2">
        <div class="card">
          <div class="card-head">
            <h2>Letzte Sicherungen</h2>
            <a href="#/backups" class="muted" style="font-size:12.5px">alle ansehen</a>
          </div>
          ${recent.length ? raw(html`
            <div class="table-wrap" style="border:0">
              <table>
                <thead><tr><th>Container</th><th>Zeitpunkt</th><th>Größe</th>
                  <th>Dauer</th><th>Status</th></tr></thead>
                <tbody>
                  ${recent.map((b) => html`
                    <tr>
                      <td><a href="#/backups/${b.container}">${b.container}</a></td>
                      <td class="nowrap muted">${ago(b.created_at)}</td>
                      <td class="nowrap mono">${bytes(b.archive_bytes)}</td>
                      <td class="nowrap muted">${duration(b.duration_s)}</td>
                      <td><span class="badge ${statusClass(b.status)}">${statusLabel(b.status)}</span></td>
                    </tr>`)}
                </tbody>
              </table>
            </div>`) : raw('<div class="empty"><strong>Noch keine Sicherung</strong>'
                         + 'Starte oben rechts mit „Alles sichern".</div>')}
        </div>

        <div class="card">
          <div class="card-head">
            <h2>Ungeschützte Container</h2>
            <a href="#/containers" class="muted" style="font-size:12.5px">Container verwalten</a>
          </div>
          ${status.unprotected_containers.length ? raw(html`
            <div class="stack">
              ${status.unprotected_containers.map((name) => html`
                <div class="spread" style="padding:7px 0;border-bottom:1px solid var(--border)">
                  <span>${name}</span>
                  <button class="btn sm" data-act="backup-one" data-name="${name}">Jetzt sichern</button>
                </div>`)}
            </div>`) : raw('<div class="notice ok">Jeder Container hat mindestens eine Sicherung.</div>')}
        </div>
      </div>`;

    on(root, '[data-act="backup-one"]', async (button) => {
      button.disabled = true;
      try {
        await api.startBackup(button.dataset.name);
        toast(`Backup von ${button.dataset.name} gestartet`, 'ok');
      } catch (error) { toast(error.message, 'err'); button.disabled = false; }
    });

    on(root, '[data-act="restore"]', (button) => openRestoreWizard(button.dataset.ref));

    on(actions, '[data-act="rescan"]', async () => {
      const result = await api.rescan();
      toast(`${result.found} Backup-Ordner geprüft, ${result.added} neu indexiert`,
            result.added ? 'ok' : 'info');
      if (result.added) navigate('dashboard');
    });

    on(actions, '[data-act="backup-all"]', async () => {
      const ok = await confirm({
        title: 'Alle Container sichern',
        message: `Es werden ${status.counts.containers} Container nacheinander gesichert. `
               + `Laufende Container werden dafür kurz angehalten, sofern das in den `
               + `Einstellungen aktiviert ist.`,
        confirmLabel: 'Sammel-Backup starten',
      });
      if (!ok) return;
      try {
        const result = await api.startBulkBackup({ all_containers: true });
        toast(`Sammel-Backup für ${result.containers.length} Container gestartet`, 'ok');
      } catch (error) { toast(error.message, 'err'); }
    });
  },
};

function healthNotices(status) {
  const notices = [];
  if (!status.docker.available) {
    notices.push(`<div class="notice err"><strong>Docker nicht erreichbar.</strong>
      Ist <code>/var/run/docker.sock</code> in den Container gemountet?</div>`);
  }
  if (!status.unraid.templates_available) {
    notices.push(`<div class="notice warn"><strong>Unraid-Templates nicht gefunden.</strong>
      Ohne den Mount von <code>/boot/config</code> können gelöschte Container zwar
      technisch wiederhergestellt werden, tauchen aber nicht im Docker-Tab der Unraid-WebGUI auf.
      Erwarteter Pfad: <code>${status.unraid.templates_dir}</code></div>`);
  }
  if (status.storage.total && status.storage.free / status.storage.total < 0.1) {
    notices.push(`<div class="notice warn"><strong>Wenig Speicherplatz.</strong>
      Nur noch ${bytes(status.storage.free)} frei auf dem Backup-Ziel.</div>`);
  }
  return notices.length ? `<div class="stack" style="margin-bottom:16px">${notices.join('')}</div>` : '';
}
