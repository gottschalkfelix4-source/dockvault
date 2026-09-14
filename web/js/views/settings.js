import { api } from '../api.js';
import { html, raw, bytes, esc, on } from '../util.js';
import { toast } from '../components.js';

export default {
  title: 'Einstellungen',
  subtitle: 'Sicherungsverhalten, Aufbewahrung und Pfade',

  async render(root, { actions }) {
    const [{ settings, restore_roots }, status] = await Promise.all([
      api.settings(), api.status(),
    ]);

    actions.innerHTML = `<button class="btn primary" data-act="save">Speichern</button>`;

    root.innerHTML = html`
      <div class="grid cols-2">
        <div class="card">
          <div class="card-head"><h2>Sicherung</h2></div>
          <div class="stack">
            <label class="field">Komprimierung
              <select data-key="compression">
                ${['zstd', 'gzip', 'none'].map((value) => html`
                  <option value="${value}" ${settings.compression === value ? raw('selected') : ''}>
                    ${value === 'zstd' ? 'zstd — schnell und stark (empfohlen)'
                      : value === 'gzip' ? 'gzip — maximale Kompatibilität'
                      : 'keine — nur tar'}</option>`)}
              </select></label>

            <label class="field">Kompressionsstufe (1 = schnell, 19 = klein)
              <input type="number" data-key="compression_level" min="1" max="19"
                     value="${String(settings.compression_level)}"></label>

            <label class="check">
              <input type="checkbox" data-key="stop_container"
                     ${settings.stop_container ? raw('checked') : ''}>
              <span><span class="t">Container während der Sicherung anhalten</span>
                <span class="d">Sorgt für konsistente Datenbanken und Dateien. Der Container
                  wird danach automatisch wieder gestartet. Ohne diese Option können
                  Datenbanken im Backup beschädigt sein.</span></span></label>

            <label class="check">
              <input type="checkbox" data-key="include_binds"
                     ${settings.include_binds ? raw('checked') : ''}>
              <span><span class="t">Bind-Mounts sichern</span>
                <span class="d">Alles unter <code>/mnt/user/appdata/…</code> und ähnliche Pfade.</span></span></label>

            <label class="check">
              <input type="checkbox" data-key="include_volumes"
                     ${settings.include_volumes ? raw('checked') : ''}>
              <span><span class="t">Benannte Docker-Volumes sichern</span>
                <span class="d">Erfordert den Mount von
                  <code>/var/lib/docker/volumes</code>.</span></span></label>

            <label class="check">
              <input type="checkbox" data-key="include_template"
                     ${settings.include_template ? raw('checked') : ''}>
              <span><span class="t">Unraid-Template mitsichern</span>
                <span class="d">Nötig, damit ein gelöschter Container im Docker-Tab
                  der Unraid-WebGUI wieder auftaucht.</span></span></label>

            <label class="check">
              <input type="checkbox" data-key="generate_missing_template"
                     ${settings.generate_missing_template ? raw('checked') : ''}>
              <span><span class="t">Fehlendes Template automatisch erzeugen</span>
                <span class="d">Container ohne Template (etwa aus docker compose) bekommen
                  eines aus ihrer laufenden Konfiguration gebaut.</span></span></label>

            <label class="check">
              <input type="checkbox" data-key="verify_checksums"
                     ${settings.verify_checksums ? raw('checked') : ''}>
              <span><span class="t">Prüfsummen vor dem Restore verifizieren</span>
                <span class="d">Schützt davor, beschädigte Archive einzuspielen.</span></span></label>

            <label class="field">Einzelnes Archiv überspringen ab (GB, 0 = kein Limit)
              <input type="number" data-key="max_artifact_gb" min="0"
                     value="${String(settings.max_artifact_gb)}"></label>
          </div>
        </div>

        <div class="stack">
          <div class="card">
            <div class="card-head"><h2>Aufbewahrung</h2></div>
            <div class="stack">
              <label class="check">
                <input type="checkbox" data-key="retention_enabled"
                       ${settings.retention_enabled ? raw('checked') : ''}>
                <span><span class="t">Alte Sicherungen automatisch löschen</span>
                  <span class="d">Läuft nach jedem Backup und nachts um 04:30 Uhr.
                    Das jeweils neueste Backup und angeheftete Stände bleiben immer erhalten.</span></span></label>
              <label class="field">Anzahl behalten (0 = unbegrenzt)
                <input type="number" data-key="retention_keep_last" min="0"
                       value="${String(settings.retention_keep_last)}"></label>
              <label class="field">Maximales Alter in Tagen (0 = unbegrenzt)
                <input type="number" data-key="retention_keep_days" min="0"
                       value="${String(settings.retention_keep_days)}"></label>
            </div>
          </div>

          <div class="card">
            <div class="card-head"><h2>Ausschlüsse</h2></div>
            <div class="stack">
              <label class="field">Container (einer pro Zeile)
                <textarea data-key="exclude_containers" style="min-height:70px"
                  >${(settings.exclude_containers || []).join('\n')}</textarea></label>
              <label class="field">Host-Pfade, die nie gesichert werden
                <textarea data-key="exclude_paths" style="min-height:90px"
                  >${(settings.exclude_paths || []).join('\n')}</textarea></label>
              <label class="field">Dateimuster (glob, z. B. *.sock oder **/cache/**)
                <textarea data-key="exclude_patterns" style="min-height:90px"
                  >${(settings.exclude_patterns || []).join('\n')}</textarea></label>
            </div>
          </div>
        </div>
      </div>

      <div class="card" style="margin-top:14px">
        <div class="card-head"><h2>System</h2></div>
        <div class="table-wrap" style="border:0">
          <table>
            <tr><th>Backup-Verzeichnis</th><td class="mono">${status.storage.backup_dir}</td>
              <td>${bytes(status.storage.free)} frei von ${bytes(status.storage.total)}</td></tr>
            <tr><th>Unraid-Templates</th><td class="mono">${status.unraid.templates_dir}</td>
              <td>${status.unraid.templates_available
                ? raw(`<span class="badge ok">gemountet · ${status.unraid.template_count} Templates</span>`)
                : raw('<span class="badge err">nicht gemountet</span>')}</td></tr>
            <tr><th>Docker</th><td class="mono">${status.docker.docker_version || '—'}</td>
              <td>${status.docker.available ? raw('<span class="badge ok">verbunden</span>')
                                            : raw('<span class="badge err">nicht erreichbar</span>')}</td></tr>
            <tr><th>Restore erlaubt nach</th>
              <td class="mono" colspan="2">${restore_roots.join('  ·  ')}</td></tr>
            <tr><th>Version</th><td class="mono" colspan="2">DockVault ${status.version}</td></tr>
          </table>
        </div>
      </div>`;

    on(actions, '[data-act="save"]', async (button) => {
      const patch = {};
      root.querySelectorAll('[data-key]').forEach((input) => {
        const key = input.dataset.key;
        if (input.type === 'checkbox') patch[key] = input.checked;
        else if (input.type === 'number') patch[key] = Number(input.value);
        else if (input.tagName === 'TEXTAREA') {
          patch[key] = input.value.split('\n').map((line) => line.trim()).filter(Boolean);
        } else patch[key] = input.value;
      });
      button.disabled = true;
      try {
        await api.saveSettings(patch);
        toast('Einstellungen gespeichert', 'ok');
      } catch (error) { toast(error.message, 'err'); }
      button.disabled = false;
    });
  },
};
