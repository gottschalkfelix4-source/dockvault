import { api } from '../api.js';
import { html, raw, date, ago, on, cronText, esc } from '../util.js';
import { modal, toast, confirm } from '../components.js';

const PRESETS = [
  { cron: '0 3 * * *', label: 'Täglich 03:00' },
  { cron: '0 */6 * * *', label: 'Alle 6 Stunden' },
  { cron: '0 4 * * 0', label: 'Sonntags 04:00' },
  { cron: '0 2 1 * *', label: 'Monatlich am 1.' },
];

export default {
  title: 'Zeitpläne',
  subtitle: 'Automatische Sicherungen per Cron-Ausdruck',

  async render(root, { actions, rerender }) {
    const [{ schedules }, { containers }] = await Promise.all([
      api.schedules(), api.containers().catch(() => ({ containers: [] })),
    ]);

    actions.innerHTML = `<button class="btn primary" data-act="new">Neuer Zeitplan</button>`;

    root.innerHTML = html`
      ${schedules.length ? raw(html`
        <div class="grid cols-2">
          ${schedules.map((schedule) => html`
            <div class="card">
              <div class="card-head">
                <div>
                  <h2>${schedule.name}</h2>
                  <p class="muted" style="margin:2px 0 0;font-size:12.5px">
                    ${cronText(schedule.cron)} · <span class="mono">${schedule.cron}</span></p>
                </div>
                <span class="badge ${schedule.enabled ? 'ok' : ''}">
                  ${schedule.enabled ? 'aktiv' : 'pausiert'}</span>
              </div>
              <table>
                <tr><th>Umfang</th><td>${schedule.all_containers
                  ? `Alle Container (${schedule.target_count})`
                  : `${schedule.containers.length} ausgewählt`}</td></tr>
                <tr><th>Nächster Lauf</th><td>${schedule.next_run ? date(schedule.next_run, true) : '—'}</td></tr>
                <tr><th>Letzter Lauf</th><td>${schedule.last_run
                  ? `${ago(schedule.last_run)} (${schedule.last_status || '—'})` : 'noch nie'}</td></tr>
              </table>
              ${!schedule.all_containers && schedule.containers.length ? raw(html`
                <div class="row" style="margin-top:10px">
                  ${schedule.containers.slice(0, 8).map((name) => html`<span class="badge">${name}</span>`)}
                  ${schedule.containers.length > 8
                    ? raw(`<span class="badge">+${schedule.containers.length - 8}</span>`) : ''}
                </div>`) : ''}
              <div class="ctr-actions" style="margin-top:12px">
                <button class="btn sm" data-act="run" data-id="${schedule.id}">Jetzt ausführen</button>
                <button class="btn sm" data-act="edit" data-id="${schedule.id}">Bearbeiten</button>
                <button class="btn ghost sm" data-act="toggle" data-id="${schedule.id}">
                  ${schedule.enabled ? 'Pausieren' : 'Aktivieren'}</button>
                <button class="btn ghost sm" data-act="delete" data-id="${schedule.id}">Löschen</button>
              </div>
            </div>`)}
        </div>`) : raw(`<div class="empty"><strong>Noch kein Zeitplan angelegt</strong>
          Ein täglicher Lauf um 03:00 Uhr ist für die meisten Setups ein guter Start.</div>`)}`;

    const openEditor = (existing) => showEditor(existing, containers, rerender);

    on(actions, '[data-act="new"]', () => openEditor(null));
    on(root, '[data-act="edit"]', (button) =>
      openEditor(schedules.find((s) => s.id === button.dataset.id)));

    on(root, '[data-act="run"]', async (button) => {
      try {
        await api.runSchedule(button.dataset.id);
        toast('Zeitplan wird ausgeführt', 'ok');
      } catch (error) { toast(error.message, 'err'); }
    });

    on(root, '[data-act="toggle"]', async (button) => {
      const schedule = schedules.find((s) => s.id === button.dataset.id);
      try {
        await api.updateSchedule(schedule.id, { ...schedule, enabled: !schedule.enabled });
        rerender();
      } catch (error) { toast(error.message, 'err'); }
    });

    on(root, '[data-act="delete"]', async (button) => {
      const schedule = schedules.find((s) => s.id === button.dataset.id);
      const ok = await confirm({
        title: 'Zeitplan löschen',
        message: `„${schedule.name}" wird entfernt. Bereits erstellte Backups bleiben erhalten.`,
        confirmLabel: 'Löschen', danger: true,
      });
      if (!ok) return;
      await api.deleteSchedule(schedule.id);
      toast('Zeitplan gelöscht', 'ok');
      rerender();
    });
  },
};

function showEditor(existing, containers, onSaved) {
  const selected = new Set(existing?.containers || []);
  const dialog = modal({
    title: existing ? `Zeitplan bearbeiten` : 'Neuer Zeitplan',
    wide: true,
    body: html`
      <label class="field">Name
        <input type="text" data-name value="${existing?.name || 'Nächtliche Sicherung'}"></label>

      <label class="field">Cron-Ausdruck (Minute Stunde Tag Monat Wochentag)
        <input type="text" data-cron value="${existing?.cron || '0 3 * * *'}"></label>
      <div class="row">
        ${PRESETS.map((preset) => html`
          <button class="btn sm" data-preset="${preset.cron}">${preset.label}</button>`)}
        <span class="muted" style="font-size:12.5px" data-cron-text></span>
      </div>

      <label class="check">
        <input type="checkbox" data-all ${existing?.all_containers ?? true ? raw('checked') : ''}>
        <span><span class="t">Alle Container sichern</span>
          <span class="d">Neu hinzugefügte Container werden automatisch mit erfasst.
            Ausgeschlossene Container aus den Einstellungen bleiben außen vor.</span></span>
      </label>

      <div data-picker ${existing?.all_containers ?? true ? raw('hidden') : ''}>
        <h3>Container auswählen</h3>
        <div class="grid cols-3" style="max-height:260px;overflow:auto">
          ${containers.map((container) => html`
            <label class="check">
              <input type="checkbox" data-container value="${container.name}"
                ${selected.has(container.name) ? raw('checked') : ''}>
              <span class="t">${container.name}</span>
            </label>`)}
        </div>
      </div>

      <label class="check">
        <input type="checkbox" data-enabled ${existing?.enabled ?? true ? raw('checked') : ''}>
        <span class="t">Zeitplan aktiv</span>
      </label>`,
    footer: `<button class="btn" data-close>Abbrechen</button>
             <button class="btn primary" data-save>Speichern</button>`,
  });

  const cronInput = dialog.root.querySelector('[data-cron]');
  const cronLabel = dialog.root.querySelector('[data-cron-text]');
  const updateCronText = () => { cronLabel.textContent = '→ ' + cronText(cronInput.value); };
  cronInput.addEventListener('input', updateCronText);
  updateCronText();

  on(dialog.root, '[data-preset]', (button) => {
    cronInput.value = button.dataset.preset;
    updateCronText();
  });

  const allBox = dialog.root.querySelector('[data-all]');
  allBox.addEventListener('change', () => {
    dialog.root.querySelector('[data-picker]').hidden = allBox.checked;
  });

  dialog.root.querySelector('[data-save]').addEventListener('click', async () => {
    const payload = {
      name: dialog.root.querySelector('[data-name]').value.trim() || 'Zeitplan',
      cron: cronInput.value.trim(),
      all_containers: allBox.checked,
      containers: [...dialog.root.querySelectorAll('[data-container]:checked')].map((c) => c.value),
      enabled: dialog.root.querySelector('[data-enabled]').checked,
      options: existing?.options || {},
    };
    if (!payload.all_containers && !payload.containers.length) {
      toast('Mindestens einen Container auswählen', 'warn');
      return;
    }
    try {
      if (existing) await api.updateSchedule(existing.id, payload);
      else await api.createSchedule(payload);
      toast('Zeitplan gespeichert', 'ok');
      dialog.close();
      onSaved();
    } catch (error) { toast(error.message, 'err'); }
  });
}
