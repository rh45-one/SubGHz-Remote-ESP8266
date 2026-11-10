// Pure timing/state helpers, also exercised under Node without a browser.
export class ArmedSession {
  constructor(now = () => performance.now(), onLock = () => {}) {
    this.now = now; this.onLock = onLock; this.epoch = 0; this.key = null; this.salt = null; this.until = 0;
  }
  arm(key, salt, epoch) {
    if (epoch !== this.epoch) return false;
    this.key = key; this.salt = salt; this.until = this.now() + 60000; return true;
  }
  active() { if (this.key && this.now() >= this.until) this.lock(); return Boolean(this.key); }
  remaining() { return this.active() ? Math.max(0, (this.until - this.now()) / 1000) : 0; }
  lock() { this.key = null; this.salt = null; this.until = 0; this.epoch++; this.onLock(); }
}

export class HoldGate {
  constructor(allowed, send, now = () => performance.now()) {
    this.allowed = allowed; this.send = send; this.now = now; this.started = null;
  }
  begin() { this.started = this.allowed() ? this.now() : null; }
  cancel() { this.started = null; }
  progress() { return this.started === null ? 0 : Math.min(1, (this.now() - this.started) / 800); }
  tick() {
    if (!this.allowed()) { this.cancel(); return false; }
    if (this.started !== null && this.progress() >= 1) {
      this.cancel(); this.send(); return true;
    }
    return false;
  }
}

export const hex = bytes => Array.from(new Uint8Array(bytes), b => b.toString(16).padStart(2, '0')).join('');
export function unhex(text) {
  if (!/^(?:[0-9a-f]{2})+$/.test(text)) throw new Error('Invalid device hex');
  return Uint8Array.from(text.match(/../g), part => parseInt(part, 16));
}
export async function deriveKey(pin, salt, cryptoAPI = crypto) {
  const pinBytes = new TextEncoder().encode(pin), saltBytes = unhex(salt);
  if (saltBytes.length !== 16) throw new Error('Invalid salt length');
  const input = new Uint8Array(16 + pinBytes.length); input.set(saltBytes); input.set(pinBytes, 16);
  try { return new Uint8Array(await cryptoAPI.subtle.digest('SHA-256', input)); }
  finally { input.fill(0); pinBytes.fill(0); }
}
export async function signEnvelope(key, nonce, endpoint, payload, cryptoAPI = crypto) {
  if (!/^[0-9a-f]{32}$/.test(nonce)) throw new Error('Invalid nonce');
  const message = new TextEncoder().encode(`${nonce}:${endpoint}:${payload}`);
  return {nonce, signature: hex(await cryptoAPI.subtle.sign('HMAC', key, message)), payload};
}
