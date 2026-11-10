import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto, createHash, createHmac} from 'node:crypto';
import {ArmedSession, HoldGate, deriveKey, hex, signEnvelope, validPin, DEFAULT_PIN} from '../app/static/safety.mjs';
import {normalizePreferences, loadPreferences, savePreferences, resolveSlots, PREFERENCES_KEY} from '../app/static/preferences.mjs';

test('PIN derivation and HMAC byte contract match independent Node crypto', async () => {
  const salt = '00112233445566778899aabbccddeeff';
  const raw = await deriveKey('0000', salt, webcrypto);
  const expected = createHash('sha256').update(Buffer.from(salt, 'hex')).update('0000').digest();
  assert.equal(hex(raw), expected.toString('hex'));
  const key = await webcrypto.subtle.importKey('raw', raw, {name:'HMAC', hash:'SHA-256'}, false, ['sign']);
  const nonce = 'ffeeddccbbaa99887766554433221100', endpoint = '/api/transmit', payload = '{"frequency_hz":433920000,"pulses":[350,-1050],"repeats":1}';
  const result = await signEnvelope(key, nonce, endpoint, payload, webcrypto);
  assert.equal(result.signature, createHmac('sha256', expected).update(`${nonce}:${endpoint}:${payload}`).digest('hex'));
  assert.equal(result.payload, payload);

  const fallbackRaw = await deriveKey('0000', salt, {});
  assert.equal(hex(fallbackRaw), expected.toString('hex'));
  const fallbackSigned = await signEnvelope(fallbackRaw, nonce, endpoint, payload, {});
  assert.equal(fallbackSigned.signature, result.signature);
});

test('PIN policy allows 4–12 ASCII digits and preserves leading zeroes', () => {
  assert.equal(DEFAULT_PIN, '0000');
  for (const pin of ['0000', '9876', '123456', '000012345678']) assert.equal(validPin(pin), true);
  for (const pin of ['', '000', '1234567890123', '12a4', '１２３４', 1234, null, ' 0000']) assert.equal(validPin(pin), false);
});

test('auto-lock at exactly 60s wipes references and invalidates pending unlock', () => {
  let time = 0, locks = 0;
  const session = new ArmedSession(() => time, () => locks++);
  assert.equal(session.arm({}, 'salt', session.epoch), true);
  time = 59999; assert.equal(session.active(), true);
  time = 60000; assert.equal(session.active(), false);
  assert.equal(session.key, null); assert.equal(session.salt, null); assert.equal(locks, 1);
  const epoch = session.epoch; session.lock();
  assert.equal(session.arm({}, 'salt', epoch), false);
});

test('tap, cancelled hold, locked state, and expired arming never send', () => {
  let time = 0, sends = 0, allowed = true;
  const gate = new HoldGate(() => allowed, () => sends++, () => time);
  gate.begin(); time = 799; assert.equal(gate.tick(), false); gate.cancel(); time = 900; gate.tick(); assert.equal(sends, 0);
  gate.begin(); time += 800; assert.equal(gate.tick(), true); gate.tick(); assert.equal(sends, 1);
  gate.begin(); allowed = false; time += 800; gate.tick(); assert.equal(sends, 1);
  gate.begin(); allowed = true; time += 800; gate.tick(); assert.equal(sends, 1);
});

test('display preferences persist only theme and two signal IDs, never credentials', () => {
  const values = new Map();
  const storage = {getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value)};
  assert.equal(savePreferences(storage, {theme: 'light', slots: ['garage', 'light'], pin: '123456', key: 'secret'}), true);
  assert.deepEqual(JSON.parse(values.get(PREFERENCES_KEY)), {theme: 'light', slots: ['garage', 'light']});
  assert.deepEqual(loadPreferences(storage), {theme: 'light', slots: ['garage', 'light']});
});

test('malformed or unavailable browser storage falls back safely', () => {
  const defaults = {theme: 'dark', slots: null};
  assert.deepEqual(loadPreferences({getItem: () => '{broken'}), defaults);
  assert.deepEqual(loadPreferences(null), defaults);
  assert.deepEqual(normalizePreferences({theme: 'script', slots: ['only-one']}), defaults);
  assert.equal(savePreferences({setItem: () => { throw new Error('Blocked'); }}, defaults), false);
});

test('missing or cleared assignments never resolve to a different signal', () => {
  const records = [{id: 'garage'}, {id: 'light'}];
  assert.deepEqual(resolveSlots(records, ['deleted', '']), [null, null]);
  assert.deepEqual(resolveSlots(records, ['light', 'garage']), [records[1], records[0]]);
  assert.deepEqual(resolveSlots(records, null), records);
});
