import { api, subscribe } from '../api.js';
import { html, raw, bytes, date, ago, esc, on, shortImage, statusClass, statusLabel }
  from '../util.js';
import { modal, toast, jobMonitor } from '../components.js';

export default {
  title: 'Wiederherstellen',
  subtitle: 'Gelöschte Container samt Daten, Konfiguration und Unraid-Template zurückholen',

  async render(root, { actions, rerender }) {
    const [orphans, backupData] = await Promise.all([api.orphans(), api.backups()]);

    actions.innerHTML = `<button class="btn" data-act="reload">Aktualisieren</button>`;

    // Neuestes Backup je Container
    const latest = new Map();
    for (const record of backupData.backups) {
      if (!latest.has(record.container) && record.status !== 'failed') {
        latest.set(record.container, record);
      }
    }

    root.innerHTML = html`
      ${orphans.orphan_backups.length ? raw(html`
        <div class="card" style="margin-bottom:18px;border-color:var(--warn)">
          <div class="card-head">
            <div>
              <h2>Fehlende Container mit Backup</h2>
              <p class="muted" style="margin:2px 0 0;font-size:12.5px">
                Diese Container existieren in Docker nicht mehr. DockVault kann sie
                vollständig neu anlegen — inklusive Daten, Netzwerken und Unraid-Template.</p>
            </div>
            <span class="badge warn">${String(orphans.orphan_backups.length)}</span>
          </div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>Container</th><th>Image</th><th>Letztes Backup</th>
                <th>Größe</th><th>Template</th><th></th></tr></thead>
              <tbody>
                ${orphans.orphan_backups.map((entry) => html`
                  <tr>
                    <td><strong>${entry.container}</strong></td>
                    <td class="mono">${shortImage(entry.image)}</td>
                    <td class="nowrap">${date(entry.created_at)}</td>
                    <td class="mono nowrap">${bytes(entry.archive_bytes)}</td>
                    <td>${entry.has_template ? raw('<span class="badge ok">enthalten</span>')
                                             : raw('<span class="badge warn">wird erzeugt</span>')}</td>
                    <td><button class="btn primary sm" data-restore="${entry.backup_ref}">
                      Wiederherstellen</button></td>
                  </tr>`)}
              </tbody>
            </table>
          </div>
        </div>`) : ''}

      ${orphans.orphan_templates.length ? raw(html`
        <div class="card" style="margin-bottom:18px">
          <div class="card-head">
            <div>
              <h2>Unraid-Templates ohne Container</h2>
              <p class="muted" style="margin:2px 0 0;font-size:12.5px">
                Kein DockVault-Backup vorhanden, aber Unraid kennt das Template noch —
                der Container lässt sich in der Unraid-WebGUI daraus neu anlegen (ohne Daten).</p>
            </div>
            <span class="badge">${String(orphans.orphan_templates.length)}</span>
          </div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>Name</th><th>Image</th><th>Datei</th><th></th></tr></thead>
              <tbody>
                ${orphans.orphan_templates.map((template) => html`
                  <tr>
                    <td><strong>${template.name}</strong></td>
                    <td class="mono">${shortImage(template.repository)}</td>
                    <td class="mono">${template.file}</td>
                    <td><button class="btn sm" data-template="${template.file}">Anzeigen</button></td>
                  </tr>`)}
              </tbody>
            </table>
          </div>
        </div>`) : ''}

      <div class="card">
        <div class="card-head">
          <h2>Alle Sicherungen</h2>
          <span class="muted" style="font-size:12.5px">
            ${String(latest.size)} Container · jeweils neuester Stand</span>
        </div>
        ${latest.size ? raw(html`
          <div class="table-wrap">
            <table>
              <thead><tr><th>Container</th><th>Letztes Backup</th><th>Größe</th>
                <th>Status</th><th></th></tr></thead>
              <tbody>
                ${[...latest.values()].map((record) => html`
                  <tr>
                    <td><strong>${record.container}</strong>
                      <div class="ctr-img">${shortImage(record.image)}</div></td>
                    <td class="nowrap">${date(record.created_at)}
                      <div class="muted" style="font-size:11.5px">${ago(record.created_at)}</div></td>
                    <td class="mono nowrap">${bytes(record.archive_bytes)}</td>
                    <td><span class="badge ${statusClass(record.status)}">${statusLabel(record.status)}</span></td>
                    <td class="nowrap">
                      <button class="btn sm" data-restore="${record.id}">Wiederherstellen</button>
                      <button class="btn ghost sm" data-history="${record.container}">Verlauf</button>
                    </td>
                  </tr>`)}
              </tbody>
            </table>
          </div>`) : raw(`<div class="empty"><strong>Keine Sicherungen vorhanden</strong>
            Lege unter „Container" die erste an.</div>`)}
      </div>`;

    on(root, '[data-restore]', (button) => openRestoreWizard(button.dataset.restore));
    on(root, '[data-history]', (button) => showHistory(button.dataset.history));
    on(root, '[data-template]', (button) => showRawTemplate(button.dataset.template));
    on(actions, '[data-act="reload"]', () => rerender());
  },
};

// ---------------------------------------------------------------- Verlauf

async function showHistory(container) {
  const { backups } = await api.backups(container);
  const dialog = modal({
    title: `Sicherungsverlauf · ${container}`,
    wide: true,
    body: html`
      <div class="table-wrap">
        <table>
          <thead><tr><th>Zeitpunkt</th><th>Auslöser</th><th>Größe</th>
            <th>Status</th><th></th></tr></thead>
          <tbody>
            ${backups.map((record) => html`
              <tr>
                <td class="nowrap">${date(record.created_at, true)}</td>
                <td class="muted">${record.trigger}</td>
                <td class="mono nowrap">${bytes(record.archive_bytes)}</td>
                <td><span class="badge ${statusClass(record.status)}">${statusLabel(record.status)}</span>
                  ${record.pinned ? raw('<span class="badge warn">angeheftet</span>') : ''}</td>
                <td><button class="btn sm" data-pick="${record.id}">Diesen Stand</button></td>
              </tr>`)}
          </tbody>
        </table>
      </div>`,
    footer: '<button class="btn" data-close>Schließen</button>',
  });
  on(dialog.root, '[data-pick]', (button) => {
    dialog.close();
    openRestoreWizard(button.dataset.pick);
  });
}

async function showRawTemplate(filename) {
  try {
    const xml = await api.templateFile(filename);
    modal({
      title: filename,
      subtitle: 'Unraid-Template — in der WebGUI unter Docker → Add Container anwendbar',
      wide: true,
      body: html`<textarea readonly style="min-height:400px">${xml}</textarea>`,
      footer: '<button class="btn" data-close>Schließen</button>',
    });
  } catch (error) { toast(error.message, 'err'); }
}

// ---------------------------------------------------------------- Assistent

export async function openRestoreWizard(backupRef) {
  let preview;
  try {
    preview = await api.restorePreview({ backup_ref: backupRef });
  } catch (error) { toast(error.message, 'err'); return; }

  const state = {
    restore_data: true,
    restore_template: true,
    recreate_container: true,
    start_container: true,
    replace_existing: preview.container_exists,
    wipe_target: false,
    target_name: '',
    artifacts: null,
  };

  const dialog = modal({
    title: `Wiederherstellen · ${preview.container}`,
    subtitle: `Backup vom ${date(preview.created_at, true)}`,
    wide: true,
    body: '<div data-step></div>',
    footer: `<button class="btn" data-close>Abbrechen</button>
             <button class="btn" data-back hidden>Zurück</button>
             <button class="btn primary" data-next>Weiter</button>`,
  });

  const stepBox = dialog.root.querySelector('[data-step]');
  const backButton = dialog.root.querySelector('[data-back]');
  const nextButton = dialog.root.querySelector('[data-next]');
  let step = 0;

  function paint() {
    stepBox.innerHTML = step === 0 ? stepOptions(preview, state) : stepConfirm(preview, state);
    backButton.hidden = step === 0;
    nextButton.textContent = step === 0 ? 'Weiter' : 'Jetzt wiederherstellen';
    nextButton.className = step === 0 ? 'btn primary' : 'btn danger';

    stepBox.querySelectorAll('[data-opt]').forEach((input) => {
      input.addEventListener('change', () => {
        state[input.dataset.opt] = input.type === 'checkbox' ? input.checked : input.value;
        if (input.dataset.opt === 'recreate_container' && !input.checked) {
          state.replace_existing = false;
        }
        paint();
      });
    });
    stepBox.querySelectorAll('[data-artifact]').forEach((input) => {
      input.addEventListener('change', () => {
        const checked = [...stepBox.querySelectorAll('[data-artifact]:checked')]
          .map((node) => node.value);
        const all = [...stepBox.querySelectorAll('[data-artifact]')].length;
        state.artifacts = checked.length === all ? null : checked;
      });
    });
    stepBox.querySelectorAll('[data-browse]').forEach((button) => {
      button.addEventListener('click', () => browseArtifact(backupRef, button.dataset.browse));
    });
  }

  backButton.addEventListener('click', () => { step = 0; paint(); });

  nextButton.addEventListener('click', async () => {
    if (step === 0) { step = 1; paint(); return; }
    if (!state.restore_data && !state.restore_template && !state.recreate_container) {
      toast('Nichts ausgewählt — mindestens eine Option aktivieren', 'warn');
      return;
    }
    nextButton.disabled = true;
    try {
      const payload = { backup_ref: backupRef, ...state,
                        target_name: state.target_name || null };
      const { job_id } = await api.restore(payload);
      dialog.close();
      attachMonitor(job_id, preview.container);
    } catch (error) {
      toast(error.message, 'err');
      nextButton.disabled = false;
    }
  });

  paint();
}

function stepOptions(preview, state) {
  return html`
    <div class="steps"><span class="s active">1 · Umfang</span><span class="s">2 · Bestätigen</span></div>

    ${preview.warnings.length ? raw(html`
      <div class="notice warn">
        <strong>Bitte beachten:</strong>
        <ul>${preview.warnings.map((warning) => html`<li>${warning}</li>`)}</ul>
      </div>`) : raw('<div class="notice ok">Keine Konflikte erkannt.</div>')}

    <div class="grid cols-3">
      <div class="stat"><div class="label">Image</div>
        <div class="value" style="font-size:15px">${shortImage(preview.image)}</div>
        <div class="sub">${preview.image_present ? 'lokal vorhanden' : 'wird geladen'}</div></div>
      <div class="stat"><div class="label">Container</div>
        <div class="value" style="font-size:15px">${preview.container_exists ? 'Existiert' : 'Fehlt'}</div>
        <div class="sub">${preview.container_exists ? 'wird ersetzt' : 'wird neu angelegt'}</div></div>
      <div class="stat"><div class="label">Template</div>
        <div class="value" style="font-size:15px">
          ${preview.template.present ? 'Im Backup' : 'Wird erzeugt'}</div>
        <div class="sub mono">${preview.template.target_file}</div></div>
    </div>

    <h3>Was soll wiederhergestellt werden?</h3>
    <label class="check">
      <input type="checkbox" data-opt="restore_data" ${state.restore_data ? raw('checked') : ''}>
      <span><span class="t">Daten</span>
        <span class="d">Alle gesicherten Bind-Mounts und Volumes zurück an die Originalpfade.</span></span>
    </label>
    <label class="check">
      <input type="checkbox" data-opt="restore_template" ${state.restore_template ? raw('checked') : ''}>
      <span><span class="t">Unraid-Template</span>
        <span class="d">Schreibt <code>${preview.template.target_file}</code> nach
          templates-user — damit erscheint der Container wieder im Docker-Tab der Unraid-WebGUI
          und lässt sich dort bearbeiten.
          ${preview.template.templates_dir_available ? '' :
            raw('<strong style="color:var(--err)"> Achtung: /boot/config ist nicht gemountet, dieser Schritt wird übersprungen.</strong>')}
        </span></span>
    </label>
    <label class="check">
      <input type="checkbox" data-opt="recreate_container" ${state.recreate_container ? raw('checked') : ''}>
      <span><span class="t">Container neu anlegen</span>
        <span class="d">Legt den Container exakt aus der gesicherten Konfiguration an:
          Image, Environment, Ports, Mounts, Labels, Restart-Policy und Netzwerke.
          Fehlende Netzwerke werden dabei automatisch erstellt.</span></span>
    </label>
    <label class="check">
      <input type="checkbox" data-opt="start_container" ${state.start_container ? raw('checked') : ''}>
      <span><span class="t">Anschließend starten</span>
        <span class="d">Container nach dem Anlegen direkt hochfahren.</span></span>
    </label>

    ${state.recreate_container && preview.container_exists ? raw(html`
      <label class="check">
        <input type="checkbox" data-opt="replace_existing" ${state.replace_existing ? raw('checked') : ''}>
        <span><span class="t" style="color:var(--warn)">Vorhandenen Container ersetzen</span>
          <span class="d">Der bestehende Container „${preview.container}" wird gestoppt und
            entfernt, bevor der gesicherte Stand angelegt wird.</span></span>
      </label>`) : ''}

    ${state.restore_data ? raw(html`
      <label class="check">
        <input type="checkbox" data-opt="wipe_target" ${state.wipe_target ? raw('checked') : ''}>
        <span><span class="t" style="color:var(--err)">Zielverzeichnisse vorher leeren</span>
          <span class="d">Löscht vorhandene Dateien im Zielpfad, bevor entpackt wird.
            Ohne diese Option werden Dateien aus dem Backup überschrieben, zusätzliche
            Dateien bleiben liegen.</span></span>
      </label>`) : ''}

    <label class="field">Zielname (leer lassen = Originalname)
      <input type="text" data-opt="target_name" value="${state.target_name}"
             placeholder="${preview.container}">
    </label>

    ${state.restore_data && preview.artifacts.length ? raw(html`
      <h3>Datenquellen</h3>
      <div class="table-wrap">
        <table>
          <thead><tr><th style="width:34px"></th><th>Quelle</th><th>Zielpfad</th>
            <th>Größe</th><th></th></tr></thead>
          <tbody>
            ${preview.artifacts.map((artifact) => html`
              <tr>
                <td><input type="checkbox" data-artifact value="${artifact.name}"
                     ${state.artifacts === null || state.artifacts.includes(artifact.name)
                       ? raw('checked') : ''}></td>
                <td><span class="badge">${artifact.kind}</span> ${artifact.name}
                  <div class="ctr-img">${artifact.destination}</div></td>
                <td class="mono">${artifact.target}
                  ${artifact.allowed ? '' : raw('<span class="badge err">nicht erlaubt</span>')}
                  ${artifact.exists ? raw('<span class="badge warn">existiert</span>') : ''}</td>
                <td class="mono nowrap">${bytes(artifact.source_bytes)}</td>
                <td><button class="btn ghost sm" data-browse="${artifact.name}">Inhalt</button></td>
              </tr>`)}
          </tbody>
        </table>
      </div>`) : ''}`;
}

function stepConfirm(preview, state) {
  const target = state.target_name || preview.container;
  const plan = [];
  if (state.restore_data) {
    const count = state.artifacts ? state.artifacts.length : preview.artifacts.length;
    plan.push(`${count} Datenquelle(n) werden an die Originalpfade entpackt`
            + (state.wipe_target ? ' — Zielverzeichnisse werden vorher geleert' : ''));
  }
  if (state.restore_template) {
    plan.push(`Unraid-Template <code>${esc(preview.template.target_file)}</code> wird geschrieben`
            + (preview.template.present ? ' (aus dem Backup)' : ' (neu erzeugt)'));
  }
  if (state.recreate_container) {
    if (preview.container_exists) plan.push(`Bestehender Container „${esc(target)}" wird entfernt`);
    plan.push(`Container „${esc(target)}" wird aus der gesicherten Konfiguration angelegt`);
    if (preview.missing_networks.length) {
      plan.push(`Fehlende Netzwerke werden erstellt: ${esc(preview.missing_networks.join(', '))}`);
    }
    if (!preview.image_present) plan.push(`Image <code>${esc(preview.image)}</code> wird geladen`);
  }
  if (state.start_container) plan.push(`Container wird gestartet`);

  return html`
    <div class="steps"><span class="s done">1 · Umfang</span><span class="s active">2 · Bestätigen</span></div>
    <div class="notice ${state.wipe_target || state.replace_existing ? 'warn' : 'info'}">
      <strong>Folgende Schritte werden ausgeführt:</strong>
      <ul>${raw(plan.map((line) => `<li>${line}</li>`).join(''))}</ul>
    </div>
    ${state.wipe_target ? raw(`<div class="notice err">
      <strong>Achtung:</strong> Vorhandene Dateien in den Zielverzeichnissen werden
      unwiderruflich gelöscht.</div>`) : ''}
    <p class="muted" style="margin:0;font-size:12.5px">
      Quelle: <span class="mono">${preview.backup_ref}</span> ·
      erstellt ${date(preview.created_at, true)}</p>`;
}

async function browseArtifact(backupRef, artifactName) {
  try {
    const { entries } = await api.browseBackup(backupRef, artifactName);
    modal({
      title: `Inhalt · ${artifactName}`,
      subtitle: `${entries.length} Einträge (gekürzt)`,
      wide: true,
      body: html`<div class="logbox" style="max-height:420px">${raw(entries.map((entry) =>
        `${entry.dir ? '📁' : '  '} ${esc(entry.name)}${entry.dir ? '' :
          '  ' + bytes(entry.size)}`).join('\n'))}</div>`,
      footer: '<button class="btn" data-close>Schließen</button>',
    });
  } catch (error) { toast(error.message, 'err'); }
}

function attachMonitor(jobId, container) {
  const monitor = jobMonitor(jobId, { title: `Wiederherstellung · ${container}` });
  const offProgress = subscribe('job.progress', (data) => {
    if (data.job_id === jobId) monitor.progress(data.progress, data.message);
  });
  const offLog = subscribe('job.log', (data) => {
    if (data.job_id === jobId) monitor.log(data.line, data.level);
  });
  const offDone = subscribe('job.finished', (data) => {
    if (data.job_id !== jobId) return;
    monitor.finish(data.status, data.error);
    offProgress(); offLog(); offDone();
  });
}
