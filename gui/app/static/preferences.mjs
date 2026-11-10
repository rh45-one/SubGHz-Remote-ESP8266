// Only display preferences belong in persistent browser storage, never authentication material.
export const PREFERENCES_KEY = 'subghz.display.v1';
export function normalizePreferences(value) {
  return {
    theme: value?.theme === 'light' ? 'light' : 'dark',
    slots: Array.isArray(value?.slots) && value.slots.length === 2 &&
      value.slots.every(id => typeof id === 'string' && id.length <= 128) ? [...value.slots] : null,
  };
}
export function loadPreferences(storage) {
  try { return normalizePreferences(JSON.parse(storage.getItem(PREFERENCES_KEY))); }
  catch { return normalizePreferences(null); }
}
export function savePreferences(storage, value) {
  try { storage.setItem(PREFERENCES_KEY, JSON.stringify(normalizePreferences(value))); return true; }
  catch { return false; }
}
export function resolveSlots(records, slots) {
  const ids = slots ?? [records[0]?.id || '', records[1]?.id || ''];
  return ids.map(id => records.find(record => record.id === id) || null);
}
