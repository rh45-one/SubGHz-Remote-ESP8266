import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto, createHash, createHmac} from 'node:crypto';
import {ArmedSession, HoldGate, deriveKey, hex, signEnvelope} from '../app/static/safety.mjs';

test('PIN derivation and HMAC byte contract match independent Node crypto', async () => {
  const salt = '00112233445566778899aabbccddeeff';
  const raw = await deriveKey('123456', salt, webcrypto);
  const expected = createHash('sha256').update(Buffer.from(salt, 'hex')).update('123456').digest();
  assert.equal(hex(raw), expected.toString('hex'));
  const key = await webcrypto.subtle.importKey('raw', raw, {name:'HMAC', hash:'SHA-256'}, false, ['sign']);
  const nonce = 'ffeeddccbbaa99887766554433221100', endpoint = '/api/transmit', payload = '{"frequency_hz":433920000,"pulses":[350,-1050],"repeats":1}';
  const result = await signEnvelope(key, nonce, endpoint, payload, webcrypto);
  assert.equal(result.signature, createHmac('sha256', expected).update(`${nonce}:${endpoint}:${payload}`).digest('hex'));
  assert.equal(result.payload, payload);
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
