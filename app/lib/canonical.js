'use strict';
/**
 * Proof-of-PnL canonicalization (MH5) — the exact JS counterpart of the
 * Python sealer's `json.dumps(bundle, sort_keys=True, separators=(",",":"),
 * ensure_ascii=False)`. Every number in a sealed bundle is already a string
 * (the sealer never hashes floats), so recursive key-sort + JSON.stringify
 * reproduces the canonical bytes exactly — pinned against a Python-generated
 * fixture in the tests.
 */

function canonicalStringify(value) {
  if (value === null || typeof value !== 'object') {
    if (typeof value === 'number' && !Number.isFinite(value)) {
      throw new Error('non-finite number cannot be canonicalized');
    }
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return '[' + value.map(canonicalStringify).join(',') + ']';
  }
  const keys = Object.keys(value).filter(k => value[k] !== undefined).sort();
  return '{' + keys.map(k =>
    JSON.stringify(k) + ':' + canonicalStringify(value[k])).join(',') + '}';
}

module.exports = { canonicalStringify };
