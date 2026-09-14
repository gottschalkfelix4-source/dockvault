import { api } from '../api.js';
import { html, raw, bytes, esc, on } from '../util.js';
import { toast } from '../components.js';

const SMB_VERSIONS = [
  ['3.1.1', 'SMB 3.1.1 — neueste, sicherste'],
  ['3.0', 'SMB 3.0 — empfohlen, sehr kompatibel'],
  ['2.1', 'SMB 2.1 — ältere NAS-Geräte'],
  ['1.0', 'SMB 1.0 — nur als letzter Ausweg'],
  ['auto', 'automatisch aushandeln'],
];

export default {
  title: 'Einstellungen',
  subtitle: 'Backup-Ziel, Sicherungsverhalten und Aufbewahrung',

  async render(root, { actions, rerender }) {
    const [{ settings, restore_roots }, status, store] = await Promise.all([
      api.settings(), api.status(), api.storage(),
    ]);

    actions.innerHTML = `<button class="btn primary" data-act="save">Speichern</button>`;

    root.innerHTML = html`
      ${raw(targetCard(settings, store))}

      <div class="grid cols-2">
        <div class="card">
          <div class="card-head"><h2>Sicherung</h2></div>
          <div class="stack">
            <label class="field">Sicherungsumfang
              <select data-key="mount_scope">
                <option value="appdata" ${settings.mount_scope === 'all' ? '' : raw('selected')}>
                  Nur Konfiguration — appdata und Docker-Volumes (empfohlen)</option>
                <option value="all" ${settings.mount_scope === 'all' ? raw('selected') : ''}>
                  Alle Mounts — auch Medien- und Datenpfade</option>
              </select></label>
            <div class="notice ${settings.mount_scope === 'all' ? 'warn' : 'info'}">
              ${settings.mount_scope === 'all'
                ? raw(`<strong>Achtung:</strong> Auch Medienbibliotheken und Downloads werden
                    mitgesichert. Bei Plex oder Immich können das schnell mehrere Terabyte sein.`)
                : raw(`Gesichert werden nur Pfade unterhalb von <code>appdata</code> sowie benannte
                    Docker-Volumes — also die Konfiguration. Medienbibliotheken, Downloads und
                    andere Shares bleiben außen vor. Was ein Container konkret mitnimmt, zeigt
                    „Details" auf der Container-Seite.`)}
            </div>

            <label class="field">Was als Konfiguration gilt (ein Pfad pro Zeile)
              <textarea data-key="appdata_roots" style="min-height:70px"
                >${(settings.appdata_roots || []).join('\n')}</textarea></label>

            <label class="field">Zusätzlich sichern, obwohl außerhalb (ein Pfad pro Zeile)
              <textarea data-key="include_extra_paths" style="min-height:58px"
                placeholder="/mnt/user/wichtige-daten"
                >${(settings.include_extra_paths || []).join('\n')}</textarea></label>

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
            <tr><th>SMB möglich</th><td class="mono">mount.cifs</td>
              <td>${store.cifs_available ? raw('<span class="badge ok">im Image vorhanden</span>')
                                         : raw('<span class="badge err">fehlt</span>')}</td></tr>
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
        const result = await api.saveSettings(patch);
        if (result.storage && result.storage.ok === false) {
          toast(`Gespeichert, aber das Backup-Ziel ließ sich nicht einbinden: `
              + result.storage.error, 'err', 10000);
        } else if (result.storage && result.storage.mounted) {
          toast('Gespeichert — SMB-Freigabe ist eingebunden', 'ok');
        } else {
          toast('Einstellungen gespeichert', 'ok');
        }
        rerender();
      } catch (error) { toast(error.message, 'err'); button.disabled = false; }
    });

    // --- Backup-Ziel ---------------------------------------------------
    const typeSelect = root.querySelector('[data-key="target_type"]');
    typeSelect?.addEventListener('change', () => {
      root.querySelector('[data-smb-fields]').hidden = typeSelect.value !== 'smb';
    });

    const smbParams = () => {
      const out = {};
      root.querySelectorAll('[data-key^="smb_"]').forEach((input) => {
        out[input.dataset.key] = input.value;
      });
      return out;
    };

    on(root, '[data-act="test"]', async (button) => {
      const box = root.querySelector('[data-test-result]');
      button.disabled = true;
      box.innerHTML = '<div class="notice info">Verbindung wird geprüft …</div>';
      try {
        const result = await api.testStorage(smbParams());
        box.innerHTML = html`
          <div class="notice ${result.writable ? 'ok' : 'warn'}">
            <strong>Verbindung steht.</strong>
            ${result.source} · ${bytes(result.free)} frei von ${bytes(result.total)}
            ${result.writable ? '· schreibbar'
              : raw(`· <strong>nicht schreibbar</strong>: ${esc(result.write_error || '')}`)}
            ${result.entries.length ? raw(
              `<br>Vorhandene Einträge: <span class="mono">${esc(result.entries.join(', '))}</span>`)
              : raw('<br>Die Freigabe ist leer.')}
          </div>`;
      } catch (error) {
        box.innerHTML = html`<div class="notice err"><strong>Fehlgeschlagen:</strong>
          ${error.message}</div>`;
      }
      button.disabled = false;
    });

    on(root, '[data-act="mount"]', async (button) => {
      button.disabled = true;
      try {
        await api.mountStorage();
        toast('SMB-Freigabe eingebunden', 'ok');
        rerender();
      } catch (error) { toast(error.message, 'err', 9000); button.disabled = false; }
    });

    on(root, '[data-act="unmount"]', async (button) => {
      button.disabled = true;
      try {
        await api.unmountStorage();
        toast('SMB-Freigabe ausgehängt', 'warn');
        rerender();
      } catch (error) { toast(error.message, 'err'); button.disabled = false; }
    });
  },
};

// ---------------------------------------------------------------- Backup-Ziel

function targetCard(settings, store) {
  const isSmb = settings.target_type === 'smb';
  return html`
    <div class="card" style="margin-bottom:14px;${store.ready ? '' : 'border-color:var(--err)'}">
      <div class="card-head">
        <div>
          <h2>Backup-Ziel</h2>
          <p class="muted" style="margin:2px 0 0;font-size:12.5px">
            Wohin DockVault die Sicherungsarchive schreibt</p>
        </div>
        ${store.ready
          ? raw(html`<span class="badge ok">bereit · ${bytes(store.free)} frei</span>`)
          : raw('<span class="badge err">nicht bereit</span>')}
      </div>

      ${raw(targetNotice(store, isSmb))}

      <div class="grid cols-2">
        <label class="field">Art des Ziels
          <select data-key="target_type">
            <option value="local" ${isSmb ? '' : raw('selected')}>
              Lokaler Pfad — das in den Container gemountete /backups</option>
            <option value="smb" ${isSmb ? raw('selected') : ''}>
              SMB-Freigabe — Netzlaufwerk auf einem anderen Rechner</option>
          </select></label>
        <div>
          <div class="muted" style="font-size:12.5px;margin-bottom:5px">Aktueller Zustand</div>
          <div class="mono" style="font-size:12px">
            ${store.mount_source || store.path}
            ${store.mount_fs ? raw(`<span class="badge">${esc(store.mount_fs)}</span>`) : ''}
          </div>
        </div>
      </div>

      <div data-smb-fields ${isSmb ? '' : raw('hidden')} style="margin-top:12px">
        <div class="grid cols-3">
          <label class="field">Server (Name oder IP)
            <input type="text" data-key="smb_host" value="${settings.smb_host}"
                   placeholder="192.168.188.185"></label>
          <label class="field">Freigabe
            <input type="text" data-key="smb_share" value="${settings.smb_share}"
                   placeholder="backup"></label>
          <label class="field">Unterordner (optional)
            <input type="text" data-key="smb_path" value="${settings.smb_path}"
                   placeholder="dockvault"></label>
          <label class="field">Benutzer
            <input type="text" data-key="smb_user" value="${settings.smb_user}"
                   placeholder="felix" autocomplete="off"></label>
          <label class="field">Passwort
            <input type="password" data-key="smb_password" value="${settings.smb_password}"
                   autocomplete="new-password"></label>
          <label class="field">Domäne / Arbeitsgruppe (optional)
            <input type="text" data-key="smb_domain" value="${settings.smb_domain}"
                   placeholder="WORKGROUP"></label>
          <label class="field">SMB-Version
            <select data-key="smb_version">
              ${SMB_VERSIONS.map(([value, label]) => html`
                <option value="${value}" ${settings.smb_version === value ? raw('selected') : ''}
                  >${label}</option>`)}
            </select></label>
          <label class="field">Zusätzliche mount-Optionen (optional)
            <input type="text" data-key="smb_options" value="${settings.smb_options}"
                   placeholder="sec=ntlmssp"></label>
        </div>

        <div class="row" style="margin-top:10px">
          <button class="btn" data-act="test">Verbindung testen</button>
          ${store.mounted
            ? raw('<button class="btn ghost" data-act="unmount">Aushängen</button>')
            : raw('<button class="btn" data-act="mount">Jetzt einbinden</button>')}
          <span class="muted" style="font-size:12px">
            Das Passwort wird in /config/settings.json gespeichert (nur für root lesbar)
            und nie wieder angezeigt.</span>
        </div>
        <div data-test-result style="margin-top:10px"></div>
      </div>
    </div>`;
}

function targetNotice(store, isSmb) {
  if (!store.cifs_available && isSmb) {
    return `<div class="notice err"><strong>mount.cifs fehlt im Image.</strong>
      Bitte auf eine neuere DockVault-Version aktualisieren.</div>`;
  }
  if (isSmb && !store.mounted) {
    return `<div class="notice err"><strong>Die SMB-Freigabe ist nicht eingebunden.</strong>
      Backups sind gesperrt, solange das so ist — sonst lägen sie im Container und wären
      beim nächsten Neustart weg. Häufigste Ursache: dem Container fehlt die Berechtigung
      zum Einbinden. Ergänze in den <em>Extra Parameters</em> des Containers
      <code>--cap-add SYS_ADMIN</code> und starte ihn neu.</div>`;
  }
  if (isSmb && store.mounted && !store.writable) {
    return `<div class="notice warn"><strong>Eingebunden, aber nicht beschreibbar.</strong>
      Der SMB-Benutzer braucht Schreibrechte auf der Freigabe.</div>`;
  }
  if (!isSmb) {
    return `<div class="notice info">Die Sicherungen liegen unter
      <code>${esc(store.path)}</code> im Container — also dort, wohin dieser Pfad
      beim Anlegen des Containers gemountet wurde.</div>`;
  }
  return '';
}
