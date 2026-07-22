/**
 * Metaverse worlds — a curated allowlist that recognises which of a wallet's
 * NFTs are metaverse LAND / names / wearables and which world they belong to,
 * with a deep-link into that world. Everything is READ-ONLY and presentational:
 * we classify what a wallet already holds and link out to the official world —
 * we never mint, transfer, or list anything.
 *
 * OpenSea returns a `collection` slug per item; that slug is what we match on
 * (contract addresses aren't in the item shape). The map is intentionally small
 * and honest — a slug we don't recognise is simply "other collectible", never
 * mislabelled as land.
 *
 * Pure & deterministic — unit-testable.
 */

'use strict';

// slug → { world, kind, url }. kind ∈ land | name | wearable.
const WORLDS = {
  // The Sandbox
  sandbox: { world: 'The Sandbox', kind: 'land', url: 'https://www.sandbox.game/en/map/' },
  'sandboxs-lands': { world: 'The Sandbox', kind: 'land', url: 'https://www.sandbox.game/en/map/' },
  'the-sandbox-assets': { world: 'The Sandbox', kind: 'wearable', url: 'https://www.sandbox.game/' },
  // Decentraland
  decentraland: { world: 'Decentraland', kind: 'land', url: 'https://play.decentraland.org/' },
  'decentraland-names': { world: 'Decentraland', kind: 'name', url: 'https://play.decentraland.org/' },
  'decentraland-wearables': { world: 'Decentraland', kind: 'wearable', url: 'https://play.decentraland.org/' },
  // Otherside (Yuga)
  otherdeed: { world: 'Otherside', kind: 'land', url: 'https://otherside.xyz/' },
  'otherdeed-for-otherside': { world: 'Otherside', kind: 'land', url: 'https://otherside.xyz/' },
  'otherside-koda': { world: 'Otherside', kind: 'wearable', url: 'https://otherside.xyz/' },
  // Voxels (formerly Cryptovoxels)
  voxels: { world: 'Voxels', kind: 'land', url: 'https://www.voxels.com/' },
  cryptovoxels: { world: 'Voxels', kind: 'land', url: 'https://www.voxels.com/' },
  // Somnium Space
  'somnium-space': { world: 'Somnium Space', kind: 'land', url: 'https://somniumspace.com/' },
  'somnium-space-vr': { world: 'Somnium Space', kind: 'land', url: 'https://somniumspace.com/' },
};

const KIND_LABEL = { land: 'Land parcel', name: 'World name', wearable: 'Wearable' };

function normSlug(s) { return String(s || '').trim().toLowerCase(); }

function worldFor(collectionSlug) {
  return WORLDS[normSlug(collectionSlug)] || null;
}

/**
 * Split a list of wallet NFT items into metaverse "worlds" holdings and the
 * rest. Each world item is annotated with world/kind/url. Also rolls up a
 * per-world summary.
 * @param {Array<object>} items opensea getWalletNfts items:
 *        { name, collection, token_id, image_url }.
 */
function classifyWorlds(items) {
  const list = Array.isArray(items) ? items : [];
  const worlds = [];
  const other = [];
  const byWorld = new Map();

  for (const it of list) {
    const meta = worldFor(it.collection);
    if (meta) {
      const row = {
        name: it.name || null,
        collection: it.collection || null,
        token_id: it.token_id != null ? String(it.token_id) : null,
        image_url: it.image_url || null,
        world: meta.world,
        kind: meta.kind,
        kind_label: KIND_LABEL[meta.kind] || 'Item',
        url: meta.url,
      };
      worlds.push(row);
      const g = byWorld.get(meta.world) || { world: meta.world, url: meta.url, count: 0, kinds: new Set() };
      g.count += 1; g.kinds.add(meta.kind);
      byWorld.set(meta.world, g);
    } else {
      other.push({
        name: it.name || null,
        collection: it.collection || null,
        token_id: it.token_id != null ? String(it.token_id) : null,
        image_url: it.image_url || null,
      });
    }
  }

  const summary = [...byWorld.values()]
    .map(g => ({ world: g.world, url: g.url, count: g.count, kinds: [...g.kinds] }))
    .sort((a, b) => b.count - a.count);

  return { worlds, other, summary, world_count: summary.length };
}

module.exports = { classifyWorlds, worldFor, WORLDS, KIND_LABEL };
