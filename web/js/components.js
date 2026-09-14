// Wiederverwendbare UI-Bausteine: Modal, Toast, Bestätigungsdialog.

import { html, raw, el } from './util.js';

const modalRoot = () => document.getElementById('modal-root');
const toastRoot = () => document.getElementById('toast-root');

export function toast(message, kind = 'info', ms = 4200) {
  const node = document.createElement('div');
  node.className = `toast ${kind}`;
  node.textContent = message;
  toastRoot().appendChild(node);
  setTimeout(() => {
    node.style.transition = 'opacity .25s';
    node.style.opacity = '0';
    setTimeout(() => node.remove(), 260);
  }, ms);
}

/**
 * Modal öffnen.
 * @returns {{close: function, root: HTMLElement}}
 */
export function modal({ title, subtitle = '', body, footer = '', wide = false, onClose }) {
  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop';
  backdrop.innerHTML = html`
    <div class="modal ${wide ? 'wide' : ''}">
      <div class="modal-head">
        <div>
          <h2>${title}</h2>
          ${subtitle ? raw(`<p class="muted" style="margin:3px 0 0;font-size:12.5px">${subtitle}</p>`) : ''}
        </div>
        <button class="btn ghost sm" data-close>✕</button>
      </div>
      <div class="modal-body">${raw(body)}</div>
      ${footer ? raw(`<div class="modal-foot">${footer}</div>`) : ''}
    </div>`;

  const close = () => {
    backdrop.remove();
    document.removeEventListener('keydown', onKey);
    if (onClose) onClose();
  };
  const onKey = (event) => { if (event.key === 'Escape') close(); };

  backdrop.addEventListener('click', (event) => {
    if (event.target === backdrop || event.target.closest('[data-close]')) close();
  });
  document.addEventListener('keydown', onKey);
  modalRoot().appendChild(backdrop);
  return { close, root: backdrop, body: el('.modal-body', backdrop) };
}

export function confirm({ title, message, confirmLabel = 'Bestätigen', danger = false,
                          detail = '' }) {
  return new Promise((resolve) => {
    const dialog = modal({
      title,
      body: html`<p style="margin:0">${message}</p>${detail ? raw(detail) : ''}`,
      footer: `<button class="btn" data-close>Abbrechen</button>
               <button class="btn ${danger ? 'danger' : 'primary'}" data-ok>${confirmLabel}</button>`,
      onClose: () => resolve(false),
    });
    dialog.root.querySelector('[data-ok]').addEventListener('click', () => {
      dialog.close();
      resolve(true);
    });
  });
}

/** Zeigt ein Job-Log live an und schließt sich nicht automatisch. */
export function jobMonitor(jobId, { title = 'Auftrag läuft', onFinish } = {}) {
  const dialog = modal({
    title,
    subtitle: jobId,
    body: `<div class="progress"><i style="width:0%"></i></div>
           <p class="muted" data-msg style="margin:0;font-size:12.5px">Startet …</p>
           <div class="logbox" data-log></div>`,
    footer: `<button class="btn" data-close>Schließen</button>`,
    wide: true,
  });

  const bar = dialog.root.querySelector('.progress > i');
  const msg = dialog.root.querySelector('[data-msg]');
  const log = dialog.root.querySelector('[data-log]');

  const append = (line, level = 'info') => {
    const div = document.createElement('div');
    div.className = level !== 'info' ? `l-${level}` : '';
    div.textContent = line;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
  };

  return {
    dialog,
    progress(percent, message) {
      bar.style.width = `${percent}%`;
      if (message) msg.textContent = message;
    },
    log: append,
    finish(status, error) {
      bar.parentElement.classList.add(status === 'completed' ? 'ok' : 'err');
      bar.style.width = '100%';
      msg.textContent = status === 'completed'
        ? 'Abgeschlossen.'
        : `Beendet: ${status}${error ? ' — ' + error : ''}`;
      if (onFinish) onFinish(status);
    },
  };
}
