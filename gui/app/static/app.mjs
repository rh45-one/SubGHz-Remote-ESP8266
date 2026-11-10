import {ArmedSession, HoldGate, deriveKey, hex, signEnvelope, validPin, DEFAULT_PIN} from './safety.mjs';
import {loadPreferences, savePreferences, resolveSlots} from './preferences.mjs';

const $ = id => document.getElementById(id);
const holds = new Set();
let busy = false, mustChange = true, preview = null, operation = null, simulatedMode = false;
let records = [], activeTab = 'library', toastTimer = null;
const storage = (() => { try { return window.localStorage; } catch { return null; } })();
const preferences = loadPreferences(storage);
const session = new ArmedSession(undefined, () => {
  for (const item of holds) item.cancel();
  operation?.abort(); operation = null;
  $('pin').value = ''; $('new-pin').value = ''; $('confirm-pin').value = '';
  updateArmed();
});
function cancelHolds() { for (const item of holds) item.cancel(); }
function message(text, kind = 'info') {
  clearTimeout(toastTimer);
  $('message').textContent = text;
  $('message').hidden = false;
  if ($('unlock-dialog').open) $('unlock-error').textContent = text;
  if (kind !== 'error') toastTimer = setTimeout(() => { $('message').hidden = true; }, 6000);
}
function setStatusLight(state, label, detail) {
  $('status-indicator').dataset.status = state;
  $('status-indicator').title = detail;
  $('status-label').textContent = label;
  $('device-status-dot').dataset.status = state;
}
function updateArmed() {
  const seconds = session.remaining();
  $('armed').textContent = seconds ? (mustChange ? 'PIN SETUP' : `ARMED ${Math.ceil(seconds)}s`) : 'LOCKED';
  $('countdown').value = seconds;
  $('remote-hint').textContent = seconds ? (mustChange ? 'Change default PIN in Settings.' : 'Press and hold 0.8s.') : 'Unlock, then hold to send.';
  $('unlock-remote-label').textContent = seconds ? 'Lock remote' : 'Unlock remote';
  $('settings-unlock').textContent = seconds ? `Lock / ${Math.ceil(seconds)}s` : 'Unlock';
  document.querySelector('.remote-body').dataset.armed = String(Boolean(seconds));
  document.querySelector('.display-state use').setAttribute('href', seconds && !mustChange ? '#i-radio' : '#i-lock');
}
function applyTheme() {
  cancelHolds();
  document.documentElement.dataset.theme = preferences.theme;
  document.querySelector('meta[name="theme-color"]').content = preferences.theme === 'light' ? '#fffdf9' : '#080808';
  $('theme-toggle').setAttribute('aria-label', `Switch to ${preferences.theme === 'dark' ? 'light' : 'dark'} theme`);
  if (preview) drawPreview();
}
function selectTab(tab) {
  cancelHolds();
  activeTab = ['library', 'converter', 'recorder', 'security'].includes(tab) ? tab : 'library';
  for (const section of document.querySelectorAll('.tab')) section.hidden = section.id !== activeTab;
  for (const button of document.querySelectorAll('[data-tab]')) button.setAttribute('aria-pressed', String(button.dataset.tab === activeTab));
  $('preview').hidden = !preview || !['library', 'converter', 'recorder'].includes(activeTab);
}
function applyRoute(focus = false) {
  cancelHolds();
  const settings = location.hash.startsWith('#settings');
  $('remote-page').hidden = settings;
  $('settings-page').hidden = !settings;
  if (settings) selectTab(location.hash.split('/')[1] || 'library');
  $('open-settings').setAttribute('aria-pressed', String(settings));
  if (focus) {
    (settings ? $('settings-title') : $('unlock-remote')).focus({preventScroll: true});
    window.scrollTo({top: 0, behavior: 'instant'});
  }
}
function showSettings(tab = 'library') {
  location.hash = `settings/${tab}`;
  applyRoute(true);
}
function showUnlock() {
  cancelHolds();
  if (busy) { message('Wait for the current operation.'); return; }
  if ($('unlock-dialog').open) return;
  $('pin').value = ''; $('unlock-error').textContent = ''; $('message').hidden = true;
  $('unlock-dialog').showModal();
  $('pin').focus();
}
function toggleLock() {
  if (session.active()) { session.lock(); message('Remote locked.'); }
  else showUnlock();
}
async function api(path, method = 'GET', data, signal) {
  const response = await fetch(path, {method, signal, cache: 'no-store', headers: {
    'X-Requested-With': 'subghz-gui', ...(data === undefined ? {} : {'Content-Type': 'application/json'})
  }, ...(data === undefined ? {} : {body: JSON.stringify(data)})});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || result.detail || `HTTP ${response.status}`);
  return result;
}
async function withBusy(action) {
  if (busy) throw new Error('Another operation is in progress');
  busy = true;
  try { return await action(); } finally { busy = false; }
}
async function signed(endpoint, args) {
  if (!session.active()) throw new Error('Unlock first');
  const epoch = session.epoch;
  operation = new AbortController();
  const controller = operation;
  try {
    const challenge = await api('/api/device/auth/challenge', 'GET', undefined, controller.signal);
    if (!session.active() || epoch !== session.epoch || challenge.salt !== session.salt) throw new Error('Session expired or PIN changed; unlock again');
    const envelope = await signEnvelope(session.key, challenge.nonce, `/api/${endpoint}`, JSON.stringify(args));
    if (!session.active() || epoch !== session.epoch || document.hidden) throw new Error('Controls locked');
    return await api(`/api/device/${endpoint}`, 'POST', envelope, controller.signal);
  } finally { if (operation === controller) operation = null; }
}
async function armWithPin(pin) {
  session.lock();
  const epoch = session.epoch;
  operation = new AbortController();
  const controller = operation;
  try {
    const challenge = await api('/api/device/auth/challenge', 'GET', undefined, controller.signal);
    let raw = await deriveKey(pin, challenge.salt); pin = '';
    let key = raw;
    if (globalThis.crypto?.subtle) {
      key = await crypto.subtle.importKey('raw', raw, {name: 'HMAC', hash: 'SHA-256'}, false, ['sign']);
      raw.fill(0); raw = null;
    }
    const envelope = await signEnvelope(key, challenge.nonce, '/api/auth/verify', '{}');
    if (epoch !== session.epoch || document.hidden) {
      if (key instanceof Uint8Array) key.fill(0);
      throw new Error('Unlock cancelled');
    }
    const result = await api('/api/device/auth/verify', 'POST', envelope, controller.signal);
    if (epoch !== session.epoch || document.hidden) {
      if (key instanceof Uint8Array) key.fill(0);
      throw new Error('Unlock cancelled');
    }
    mustChange = result.must_change_pin;
    session.arm(key, challenge.salt, epoch); updateArmed();
    $('unlock-dialog').close();
    $('message').hidden = true;
    refreshDeviceStatus();
    if (mustChange) {
      showSettings('security');
      message('Default PIN verified. Choose a new PIN before sending or recording.');
    }
  } finally { pin = ''; if (operation === controller) operation = null; }
}
function handler(action) { return event => { event?.preventDefault(); Promise.resolve().then(action).catch(error => message(error.name === 'AbortError' ? 'Locked; pending operation cancelled.' : error.message, 'error')); }; }
$('unlock-form').addEventListener('submit', event => {
  event.preventDefault();
  if (busy) { message('Wait for the current operation'); return; }
  let pin = $('pin').value; $('pin').value = '';
  if (!validPin(pin)) { message('PIN must be 4–12 digits'); return; }
  const pending = withBusy(() => armWithPin(pin)); pin = '';
  pending.catch(error => { session.lock(); message(error.name === 'AbortError' ? 'Unlock cancelled.' : error.message, 'error'); });
});
$('unlock-remote').addEventListener('click', toggleLock);
$('settings-unlock').addEventListener('click', toggleLock);
$('close-unlock').addEventListener('click', () => { session.lock(); $('unlock-dialog').close(); });
$('unlock-dialog').addEventListener('cancel', () => session.lock());
$('unlock-dialog').addEventListener('close', () => { $('pin').value = ''; $('unlock-error').textContent = ''; });
$('open-settings').addEventListener('click', () => showSettings());
$('back-remote').addEventListener('click', () => { location.hash = 'remote'; applyRoute(true); });
window.addEventListener('hashchange', () => applyRoute());
window.addEventListener('blur', () => session.lock());
document.addEventListener('visibilitychange', () => { if (document.hidden) session.lock(); else refreshDeviceStatus(); });
window.addEventListener('pagehide', () => session.lock());
setInterval(updateArmed, 100);

function bindHold(button, getWaveform) {
  let selected = null;
  const gate = new HoldGate(() => session.active() && !mustChange && !busy && !document.hidden &&
    !button.disabled && button.getClientRects().length > 0 && selected !== null, () => {
    const signal = selected;
    if (!signal) return;
    navigator.vibrate?.(40);
    withBusy(async () => {
      try {
        const result = await signed('transmit', {frequency_hz: signal.frequency_hz, pulses: [...signal.pulses], repeats: signal.repeats});
        message(result.simulated ? 'SIMULATED send complete — no RF emitted.' : 'Transmission completed. Locked.');
      } finally { session.lock(); }
    }).catch(error => message(`Send failed or outcome unknown: ${error.message}. Not retried.`, 'error'));
  });
  let animation = null;
  function cancel() { gate.cancel(); selected = null; button.style.setProperty('--held', '0%'); button.classList.remove('holding'); if (animation !== null) cancelAnimationFrame(animation); animation = null; }
  const item = {cancel}; holds.add(item);
  function frame() {
    button.style.setProperty('--held', `${gate.progress() * 100}%`);
    if (gate.tick() || !session.active() || busy || gate.started === null) { cancel(); return; }
    animation = requestAnimationFrame(frame);
  }
  function begin() {
    cancel();
    if (!session.active()) { showUnlock(); return; }
    if (mustChange) { showSettings('security'); message('Change the default PIN first.'); return; }
    const signal = getWaveform();
    if (!signal) { message('Assign a signal in Settings first.'); return; }
    selected = {...signal, pulses: [...signal.pulses]}; // Freeze the intended waveform at hold start.
    gate.begin();
    if (gate.started === null) { cancel(); return; }
    button.classList.add('holding'); animation = requestAnimationFrame(frame);
  }
  button.addEventListener('pointerdown', event => { if (!event.isPrimary || event.button !== 0) return; event.preventDefault(); button.setPointerCapture(event.pointerId); begin(); });
  for (const type of ['pointerup', 'pointercancel', 'pointerleave', 'lostpointercapture', 'blur']) button.addEventListener(type, cancel);
  button.addEventListener('contextmenu', event => { event.preventDefault(); cancel(); });
  button.addEventListener('keydown', event => { if (event.code === 'Space' || event.code === 'Enter') { event.preventDefault(); if (!event.repeat) begin(); } });
  button.addEventListener('keyup', cancel);
  button.addEventListener('click', event => event.preventDefault()); // Click never sends.
  return item;
}
function renderRemote() {
  cancelHolds();
  const slots = resolveSlots(records, preferences.slots);
  for (let i = 0; i < 2; ++i) {
    const signal = slots[i], button = $(`remote-${i + 1}`);
    button.disabled = !signal;
    button.setAttribute('aria-label', signal ? `${signal.name}. Hold for 0.8 seconds to send.` : `Button ${i + 1}: not assigned. Configure in Settings.`);
    $(`remote-name-${i + 1}`).textContent = signal?.name || 'Not assigned';
    $(`remote-note-${i + 1}`).textContent = signal ? 'HOLD TO SEND' : 'SET UP IN SETTINGS';
    const select = $(`slot-${i + 1}`);
    select.replaceChildren(new Option('Not assigned', ''));
    for (const record of records) select.add(new Option(record.name, record.id));
    select.value = signal?.id || '';
  }
  const frequencies = [...new Set(slots.filter(Boolean).map(signal => signal.frequency_hz))];
  $('remote-frequency').textContent = frequencies.length === 1 ? `${(frequencies[0] / 1e6).toFixed(2)} MHz` : (frequencies.length ? 'MULTI / OOK' : 'NO SIGNALS');
}
async function refreshLibrary() {
  records = await api('/api/signals');
  if (preferences.slots === null && records.length) {
    // Pick initial defaults once; deleting a signal must never silently remap a familiar button.
    preferences.slots = [records[0]?.id || '', records[1]?.id || ''];
    savePreferences(storage, preferences);
  }
  renderRemote();
  $('signals').replaceChildren();
  if (!records.length) {
    const empty = document.createElement('div'); empty.className = 'empty-library';
    const heading = document.createElement('h3'); heading.textContent = 'Your library is empty'; empty.append(heading);
    const copy = document.createElement('p'); copy.textContent = 'Import a .sub file or record your first signal.'; empty.append(copy);
    const importButton = document.createElement('button'); importButton.textContent = 'Import a signal'; importButton.addEventListener('click', () => showSettings('converter')); empty.append(importButton);
    $('signals').append(empty);
  }
  for (const signal of records) {
    const card = document.createElement('article'); card.className = 'card';
    const title = document.createElement('h3'); title.textContent = signal.name; card.append(title);
    const info = document.createElement('p'); info.className = 'signal-meta'; info.textContent = `${signal.category || 'Uncategorized'} · ${(signal.frequency_hz / 1e6).toFixed(3)} MHz · ${signal.pulses.length} pulses × ${signal.repeats}`; card.append(info);
    const actions = document.createElement('div'); actions.className = 'signal-actions'; card.append(actions);
    const view = document.createElement('button'); view.textContent = 'Preview'; view.addEventListener('click', () => showPreview(signal)); actions.append(view);
    const exportLink = document.createElement('a'); exportLink.textContent = 'Export .sub'; exportLink.href = `/api/signals/${encodeURIComponent(signal.id)}/export`; exportLink.download = 'signal.sub'; actions.append(exportLink);
    const edit = document.createElement('button'); edit.textContent = 'Rename'; edit.addEventListener('click', handler(async () => {
      const name = window.prompt('Signal name', signal.name); if (name === null) return;
      const category = window.prompt('Category', signal.category); if (category === null) return;
      const {id, ...data} = signal; await api(`/api/signals/${encodeURIComponent(id)}`, 'PUT', {...data, name, category}); await refreshLibrary();
    })); actions.append(edit);
    const remove = document.createElement('button'); remove.textContent = 'Delete'; remove.className = 'delete-button'; remove.addEventListener('click', handler(async () => { if (window.confirm(`Delete ${signal.name}?`)) { await api(`/api/signals/${encodeURIComponent(signal.id)}`, 'DELETE'); await refreshLibrary(); } })); actions.append(remove);
    $('signals').append(card);
  }
}
function showPreview(signal) {
  for (const item of holds) item.cancel(); // A new preview must not replace a waveform mid-hold.
  preview = {frequency_hz: signal.frequency_hz, pulses: [...signal.pulses], repeats: signal.repeats, source_protocol: signal.source_protocol || 'RAW', warnings: signal.warnings || []};
  $('preview').hidden = false; $('signal-name').value = signal.name || 'Recorded signal'; $('signal-category').value = signal.category || '';
  $('preview-info').textContent = `${preview.source_protocol}: ${preview.pulses.length} pulses × ${preview.repeats}, ${preview.frequency_hz} Hz. ${preview.warnings.join('. ')}`;
  drawPreview();
}
function drawPreview() {
  if (!preview) return;
  const canvas = $('waveform'), ctx = canvas.getContext('2d'), duration = preview.pulses.reduce((sum, p) => sum + Math.abs(p), 0);
  ctx.clearRect(0, 0, canvas.width, canvas.height); ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim(); ctx.lineWidth = 2; ctx.beginPath(); let x = 0;
  ctx.moveTo(0, 120);
  for (const pulse of preview.pulses) { const y = pulse > 0 ? 30 : 120; ctx.lineTo(x, y); x += Math.abs(pulse) / duration * canvas.width; ctx.lineTo(x, y); } ctx.stroke();
}
bindHold($('test-send'), () => preview);
for (let i = 0; i < 2; ++i) {
  bindHold($(`remote-${i + 1}`), () => resolveSlots(records, preferences.slots)[i]);
  $(`slot-${i + 1}`).addEventListener('change', () => {
    preferences.slots = [$('slot-1').value, $('slot-2').value];
    if (!savePreferences(storage, preferences)) message('Assignment changed for this visit; browser storage is unavailable.');
    renderRemote();
  });
}
$('close-preview').addEventListener('click', () => { cancelHolds(); preview = null; $('preview').hidden = true; });
$('refresh').addEventListener('click', handler(refreshLibrary));
$('convert').addEventListener('click', handler(async () => showPreview(await api('/api/flipper/convert', 'POST', {text: $('sub-text').value, name: 'Imported signal'}))));
$('sub-file').addEventListener('change', handler(async () => { const file = $('sub-file').files[0]; if (!file) return; if (file.size > 262144) throw new Error('Maximum file size is 256 KiB'); $('sub-text').value = await file.text(); }));
$('save').addEventListener('click', handler(async () => { if (!preview) return; await api('/api/signals', 'POST', {...preview, name: $('signal-name').value, category: $('signal-category').value}); await refreshLibrary(); message('Saved to Docker signal library'); }));
$('export-preview').addEventListener('click', handler(async () => {
  if (!preview) return;
  const data = await api('/api/flipper/export', 'POST', {frequency_hz: preview.frequency_hz, pulses: preview.pulses, repeats: preview.repeats});
  const url = URL.createObjectURL(new Blob([data.text], {type: 'text/plain'}));
  const link = document.createElement('a'); link.href = url; link.download = 'signal.sub'; link.click(); URL.revokeObjectURL(url);
}));
$('record').addEventListener('click', handler(() => withBusy(async () => {
  if (mustChange) throw new Error('Change default PIN first');
  await signed('record/start', {frequency_hz: Number($('frequency').value), window_ms: 10000}); message('Capture requested. Check capture status in up to 10 seconds.');
})));
$('poll').addEventListener('click', handler(async () => { const result = await api('/api/device/record/status'); if (result.state === 2) showPreview(result); else message(`Capture state: ${['idle', 'recording', 'complete', 'timeout', 'overflow'][result.state] || 'unknown'}`); }));
$('status').addEventListener('click', handler(async () => { const st = await api('/api/device/status'); $('diagnostics').textContent = JSON.stringify(st, null, 2); $('diagnostics').hidden = false; refreshDeviceStatus(); }));
$('change-form').addEventListener('submit', handler(() => withBusy(async () => {
  let pin = $('new-pin').value;
  let confirm = $('confirm-pin').value;
  $('new-pin').value = ''; $('confirm-pin').value = '';
  try {
    if (!validPin(pin) || pin !== confirm || pin === DEFAULT_PIN) throw new Error('Use matching 4–12 digit PINs other than 0000');
    confirm = '';
    const rand = new Uint8Array(16);
    if (globalThis.crypto?.getRandomValues) crypto.getRandomValues(rand);
    else for (let i = 0; i < 16; i++) rand[i] = Math.floor(Math.random() * 256);
    const salt = hex(rand);
    const raw = await deriveKey(pin, salt); pin = '';
    const new_pin_key = hex(raw); raw.fill(0);
    await signed('auth/change-pin', {salt, new_pin_key}); mustChange = false; refreshDeviceStatus(); message('PIN changed. Unlock again.');
  } finally { pin = ''; confirm = ''; session.lock(); }
})));
for (const button of document.querySelectorAll('[data-tab]')) button.addEventListener('click', () => showSettings(button.dataset.tab));
$('theme-toggle').addEventListener('click', () => {
  preferences.theme = preferences.theme === 'dark' ? 'light' : 'dark';
  savePreferences(storage, preferences); applyTheme();
});
let statusChecking = false;
async function refreshDeviceStatus() {
  if (statusChecking) return;
  statusChecking = true;
  try {
    const st = await api('/api/device/status');
    mustChange = Boolean(st.must_change_pin);
    updateArmed();
    if (st.simulated || simulatedMode) {
      $('mode').textContent = 'SIMULATED';
      setStatusLight('simulated', 'SIMULATED', 'Simulated device — CC1101 simulated');
    } else if (!st.cc1101_ok) {
      $('mode').textContent = 'CC1101 FAULT';
      setStatusLight('offline', 'CC1101 FAULT', 'ESP8266 reachable, but CC1101 is disconnected or unhealthy');
    } else if (!st.auth_ready) {
      $('mode').textContent = 'AUTH FAULT';
      setStatusLight('warning', 'AUTH FAULT', 'ESP8266 + CC1101 reachable, but auth store is uninitialized');
    } else {
      $('mode').textContent = 'LIVE DEVICE';
      setStatusLight('online', 'ONLINE', `ESP8266 reachable · CC1101 healthy (uptime ${st.uptime_s}s)`);
    }
  } catch {
    if (!simulatedMode) {
      $('mode').textContent = 'OFFLINE';
      setStatusLight('offline', 'OFFLINE', 'ESP8266 unreachable');
    }
  } finally {
    statusChecking = false;
  }
}
applyTheme(); applyRoute(); updateArmed();
api('/api/config').then(config => {
  simulatedMode = Boolean(config.simulated);
  $('mode').textContent = config.simulated ? 'SIMULATED' : 'LIVE DEVICE';
  $('mode').title = config.simulated ? 'SIMULATED DEVICE — NO RF / NO HARDWARE' : 'Real device mode — requires configured ESP8266';
  $('settings-mode').textContent = config.simulated ? 'SIMULATED DEVICE / NO RF' : 'REAL DEVICE / AUTHENTICATION REQUIRED';
  if (config.simulated) setStatusLight('simulated', 'SIMULATED', 'Simulated device — no RF hardware connected');
  refreshDeviceStatus();
}).catch(error => {
  $('mode').textContent = 'OFFLINE';
  $('settings-mode').textContent = 'DEVICE OFFLINE';
  setStatusLight('offline', 'OFFLINE', 'Backend or ESP8266 offline');
  message(error.message, 'error');
});
setInterval(() => { if (!document.hidden) refreshDeviceStatus(); }, 3000);
refreshLibrary().catch(error => message(error.message, 'error'));
