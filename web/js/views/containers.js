import { api } from '../api.js';
import { html, raw, bytes, ago, date, esc, on, stateLabel, statusClass, statusLabel,
         shortImage } from '../util.js';
import { modal, toast, confirm } from '../components.js';
import { openRestoreWizard } from './restore.js';

let filter = '';
let onlyUnprotected = false;

export default {
  title: 'Container',
  subtitle: 'Sichern, steuern und Templates erzeugen',

  async render(root, { actions, rerender }) {
    const { containers } = await api.containers();

    actions.innerHTML = `
      <input type="text" data-search placeholder="Suchen …" value="${esc(filter)}"
             style="width:180px">
      <label class="check" style="padding:0">
        <input type="checkbox" data-unprotected ${onlyUnprotected ? 'checked' : ''}>
        <span class="t">nur ungesichert</span></label>
      <button class="btn primary" data-act="bulk">Auswahl sichern</button>`;

    const visible = containers.filter((c) => {
      if (onlyUnprotected && c.backup.protected) return false;
      if (!filter) return true;
      const needle = filter.toLowerCase();
      return c.name.toLowerCase().includes(needle) || (c.image || '').toLowerCase().includes(needle);
    });

    root.innerHTML = html`
      <p class="muted" style="margin:0 0 14px;font-size:12.5px">
        ${String(visible.length)} von ${String(containers.length)} Containern
        · Auswahl per Kästchen, dann oben „Auswahl sichern"</p>
      <div class="grid cols-3">
        ${visible.map(card)}
      </div>
      ${visible.length ? '' : raw('<div class="empty"><strong>Keine Treffer</strong>Filter anpassen.</div>')}`;

    // --- Filter --------------------------------------------------------
    const search = actions.querySelector('[data-search]');
    search.addEventListener('input', (event) => {
      filter = event.target.value;
      clearTimeout(search._timer);
      search._timer = setTimeout(() => rerender(), 200);
    });
    actions.querySelector('[data-unprotected]').addEventListener('change', (event) => {
      onlyUnprotected = event.target.checked;
      rerender();
    });

    // --- Aktionen ------------------------------------------------------
    on(root, '[data-act="backup"]', async (button) => {
      button.disabled = true;
      button.textContent = 'Startet …';
      try {
        await api.startBackup(button.dataset.name);
        toast(`Backup von ${button.dataset.name} gestartet`, 'ok');
      } catch (error) {
        toast(error.message, 'err');
        button.disabled = false;
        button.textContent = 'Sichern';
      }
    });

    on(root, '[data-act="detail"]', (button) => showContainerDetail(button.dataset.name));
    on(root, '[data-act="logs"]', (button) => showLogs(button.dataset.name));
    on(root, '[data-act="template"]', (button) => showTemplate(button.dataset.name));

    on(root, '[data-act="power"]', async (button) => {
      const { name, action } = button.dataset;
      if (action === 'remove') {
        const ok = await confirm({
          title: `Container „${name}" entfernen`,
          message: 'Der Container wird gelöscht. Die Daten in den Mounts bleiben erhalten, '
                 + 'und du kannst ihn aus einem Backup komplett zurückholen.',
          confirmLabel: 'Entfernen', danger: true,
        });
        if (!ok) return;
      }
      button.disabled = true;
      try {
        await api.containerAction(name, action);
        toast(`${name}: ${action} ausgeführt`, 'ok');
        setTimeout(() => rerender(), 600);
      } catch (error) { toast(error.message, 'err'); button.disabled = false; }
    });

    on(actions, '[data-act="bulk"]', async () => {
      const selected = [...root.querySelectorAll('[data-select]:checked')].map((c) => c.value);
      if (!selected.length) { toast('Keine Container ausgewählt', 'warn'); return; }
      const ok = await confirm({
        title: `${selected.length} Container sichern`,
        message: 'Die Container werden nacheinander gesichert und dafür gegebenenfalls '
               + 'kurz angehalten.',
        confirmLabel: 'Starten',
      });
      if (!ok) return;
      try {
        await api.startBulkBackup({ containers: selected });
        toast(`Sammel-Backup für ${selected.length} Container gestartet`, 'ok');
      } catch (error) { toast(error.message, 'err'); }
    });
  },
};

function card(container) {
  const b = container.backup;
  const cls = container.busy ? 'busy' : (!b.protected && !container.excluded ? 'unprotected' : '');
  return html`
    <div class="ctr-card ${cls}">
      <div class="ctr-head">
        <label class="check" style="padding:0;margin-top:9px">
          <input type="checkbox" data-select value="${container.name}"
                 ${container.excluded ? raw('disabled') : ''}>
        </label>
        ${container.icon
          ? raw(`<img class="ctr-icon" src="${esc(container.icon)}" alt=""
                   onerror="this.replaceWith(Object.assign(document.createElement('div'),
                            {className:'ctr-icon',textContent:'${esc(container.name.slice(0, 2).toUpperCase())}'}))">`)
          : raw(`<div class="ctr-icon">${esc(container.name.slice(0, 2).toUpperCase())}</div>`)}
        <div style="min-width:0;flex:1">
          <div class="ctr-name">${container.name}</div>
          <div class="ctr-img">${shortImage(container.image)}</div>
        </div>
      </div>

      <div class="ctr-meta">
        <span class="badge ${container.running ? 'ok' : ''}">${stateLabel(container.state)}</span>
        ${b.protected
          ? raw(html`<span class="badge ${statusClass(b.last_status)}">
              ${String(b.count)}× · ${ago(b.last)}</span>`)
          : raw('<span class="badge warn">ungesichert</span>')}
        ${b.bytes ? raw(html`<span class="badge">${bytes(b.bytes)}</span>`) : ''}
        ${container.has_template ? raw('<span class="badge">Template</span>')
                                 : raw('<span class="badge warn">kein Template</span>')}
        ${container.excluded ? raw('<span class="badge info">ausgeschlossen</span>') : ''}
      </div>

      <div class="ctr-actions">
        <button class="btn primary sm" data-act="backup" data-name="${container.name}"
          ${container.excluded || container.busy ? raw('disabled') : ''}>Sichern</button>
        <button class="btn sm" data-act="detail" data-name="${container.name}">Details</button>
        <button class="btn sm" data-act="logs" data-name="${container.name}">Log</button>
        ${!container.has_template
          ? raw(html`<button class="btn sm" data-act="template" data-name="${container.name}">
              Template erzeugen</button>`) : ''}
        ${container.running
          ? raw(html`<button class="btn ghost sm" data-act="power" data-action="stop"
                       data-name="${container.name}">Stoppen</button>`)
          : raw(html`<button class="btn ghost sm" data-act="power" data-action="start"
                       data-name="${container.name}">Starten</button>`)}
        <button class="btn ghost sm" data-act="power" data-action="restart"
                data-name="${container.name}">Neustart</button>
      </div>
    </div>`;
}

// ---------------------------------------------------------------- Detail

async function showContainerDetail(name) {
  const [detail, plan] = await Promise.all([
    api.container(name),
    api.backupPlan(name).catch((error) => ({ error: error.message })),
  ]);
  const summary = detail.summary;

  const dialog = modal({
    title: name,
    subtitle: shortImage(summary.image),
    wide: true,
    body: html`
      <div class="grid cols-3">
        <div class="stat"><div class="label">Status</div>
          <div class="value" style="font-size:18px">${stateLabel(summary.state)}</div>
          <div class="sub">${summary.health || summary.restart_policy || ''}</div></div>
        <div class="stat"><div class="label">Sicherungen</div>
          <div class="value" style="font-size:18px">${String(detail.backups.length)}</div>
          <div class="sub">${detail.backups[0] ? ago(detail.backups[0].created_at) : 'noch keine'}</div></div>
        <div class="stat"><div class="label">Unraid-Template</div>
          <div class="value" style="font-size:18px">${detail.template.present ? 'Vorhanden' : 'Fehlt'}</div>
          <div class="sub mono">${detail.template.file || 'wird bei Bedarf erzeugt'}</div></div>
      </div>

      <h3>Was würde gesichert?</h3>
      ${plan.error ? raw(html`<div class="notice err">${plan.error}</div>`) : raw(html`
        <div class="table-wrap">
          <table>
            <thead><tr><th>Art</th><th>Host-Pfad</th><th>Im Container</th>
              <th>Größe</th><th>Dateien</th><th>gefiltert</th></tr></thead>
            <tbody>
              ${plan.artifacts.map((a) => html`
                <tr>
                  <td><span class="badge ok">${a.kind}</span></td>
                  <td class="mono">${a.source}</td>
                  <td class="mono">${a.destination}</td>
                  <td class="mono nowrap">${bytes(a.estimated_bytes)}</td>
                  <td class="mono">${String(a.files)}</td>
                  <td class="mono nowrap">${a.excluded_bytes
                    ? raw(`<span class="badge warn" title="${esc(String(a.excluded_files))} Datei(en) durch Dateimuster ausgelassen">−${esc(bytes(a.excluded_bytes))}</span>`)
                    : '—'}</td>
                </tr>`)}
              ${plan.artifacts.length ? '' : raw(`<tr><td colspan="6" class="muted">
                Keine Konfigurationspfade — es werden nur Container-Konfiguration
                und Template gesichert.</td></tr>`)}
            </tbody>
          </table>
        </div>
        <p class="muted" style="margin:0;font-size:12.5px">
          Geschätztes Volumen: <strong>${bytes(plan.estimated_bytes)}</strong> vor Komprimierung</p>

        ${plan.excluded_by_patterns_bytes ? raw(html`
          <div class="notice info">
            <strong>${bytes(plan.excluded_by_patterns_bytes)}</strong> wurden von den
            Dateimustern herausgefiltert und fehlen deshalb in der Summe — das erklärt den
            Unterschied zu <span class="mono">du -sh</span>. Aktive Muster:
            <span class="mono">${(plan.exclude_patterns || []).join('  ')}</span>.
            Änderbar unter <a href="#/settings">Einstellungen → Ausschlüsse</a>.
          </div>`) : ''}

        ${(plan.skipped || []).length ? raw(html`
          <h3 style="margin-top:6px">Bewusst übersprungen</h3>
          <div class="table-wrap">
            <table>
              <thead><tr><th>Art</th><th>Host-Pfad</th><th>Im Container</th>
                <th>Grund</th></tr></thead>
              <tbody>
                ${plan.skipped.map((a) => html`
                  <tr style="opacity:.7">
                    <td><span class="badge warn">${a.kind}</span></td>
                    <td class="mono">${a.source}</td>
                    <td class="mono">${a.destination}</td>
                    <td class="muted">${a.reason}</td>
                  </tr>`)}
              </tbody>
            </table>
          </div>
          <p class="muted" style="margin:0;font-size:12.5px">
            Datenpfade werden nicht mitgesichert. Soll einer davon doch hinein, trag ihn
            unter <a href="#/settings">Einstellungen → „Zusätzlich sichern"</a> ein.</p>`) : ''}`)}

      <h3>Netzwerk &amp; Ports</h3>
      <div class="row">
        ${summary.networks.map((n) => html`<span class="badge info">${n}</span>`)}
        ${summary.ports.map((p) => html`<span class="badge">${p.host} → ${p.container}</span>`)}
      </div>

      ${detail.backups.length ? raw(html`
        <h3>Sicherungsverlauf</h3>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Zeitpunkt</th><th>Größe</th><th>Status</th><th></th></tr></thead>
            <tbody>
              ${detail.backups.slice(0, 10).map((b) => html`
                <tr>
                  <td class="nowrap">${date(b.created_at)}</td>
                  <td class="mono nowrap">${bytes(b.archive_bytes)}</td>
                  <td><span class="badge ${statusClass(b.status)}">${statusLabel(b.status)}</span></td>
                  <td><button class="btn sm" data-restore-ref="${b.id}">Wiederherstellen</button></td>
                </tr>`)}
            </tbody>
          </table>
        </div>`) : ''}`,
    footer: `<button class="btn" data-close>Schließen</button>
             <button class="btn primary" data-backup-now>Jetzt sichern</button>`,
  });

  dialog.root.querySelector('[data-backup-now]').addEventListener('click', async () => {
    try {
      await api.startBackup(name);
      toast(`Backup von ${name} gestartet`, 'ok');
      dialog.close();
    } catch (error) { toast(error.message, 'err'); }
  });
  on(dialog.root, '[data-restore-ref]', (button) => {
    dialog.close();
    openRestoreWizard(button.dataset.restoreRef);
  });
}

async function showLogs(name) {
  const dialog = modal({
    title: `Log · ${name}`,
    wide: true,
    body: '<div class="logbox" data-log>Lade …</div>',
    footer: `<button class="btn" data-close>Schließen</button>
             <button class="btn" data-reload>Neu laden</button>`,
  });
  const load = async () => {
    const box = dialog.root.querySelector('[data-log]');
    try {
      box.textContent = (await api.containerLogs(name, 400)) || '(leer)';
      box.scrollTop = box.scrollHeight;
    } catch (error) { box.textContent = 'Fehler: ' + error.message; }
  };
  dialog.root.querySelector('[data-reload]').addEventListener('click', load);
  load();
}

async function showTemplate(name) {
  let generated;
  try {
    generated = await api.generateTemplate(name, false);
  } catch (error) { toast(error.message, 'err'); return; }

  const dialog = modal({
    title: `Unraid-Template für „${name}"`,
    subtitle: 'Aus der laufenden Container-Konfiguration erzeugt',
    wide: true,
    body: html`
      <div class="notice info">
        Dieser Container hat kein Unraid-Template und taucht deshalb nicht im Docker-Tab
        der WebGUI auf. Das folgende Template bildet Image, Ports, Pfade, Variablen und
        Labels ab. Nach dem Speichern erscheint der Container in Unraid und kann dort
        bearbeitet werden.
      </div>
      <textarea readonly style="min-height:320px">${generated.xml}</textarea>`,
    footer: `<button class="btn" data-close>Abbrechen</button>
             <button class="btn primary" data-save>In Unraid speichern</button>`,
  });

  dialog.root.querySelector('[data-save]').addEventListener('click', async () => {
    try {
      const result = await api.generateTemplate(name, true);
      toast(`Template gespeichert: ${result.file}`, 'ok');
      dialog.close();
    } catch (error) { toast(error.message, 'err'); }
  });
}
