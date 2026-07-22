/**
 * Web3 identity & metaverse worlds — REST surface, JWT-authed, READ-ONLY.
 *
 * Resolves the caller's OWN SIWE-linked wallet (users.wallet_address) and
 * surfaces: their ENS identity (name + avatar), and their NFT collectibles
 * split into metaverse "worlds" (LAND / names / wearables, each with a deep-link
 * into the official world) vs everything else. Nothing here mints, transfers,
 * or lists — it mirrors what the wallet already holds and links out.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { walletAddressOf } = require('../lib/wallet');
const { resolveIdentity } = require('../lib/ens');
const { getWalletNfts } = require('../lib/opensea');
const { classifyWorlds } = require('../lib/worlds');

const router = express.Router();
router.use(authMiddleware);
router.use(rateLimit({ windowMs: 60000, max: 20, key: userKey }));

// GET /api/web3/identity — the caller's ENS identity for their linked wallet.
router.get('/identity', async (req, res) => {
  try {
    const address = await walletAddressOf(req.user.user_id);
    if (!address) return res.json({ read_only: true, linked: false });
    const identity = await resolveIdentity(address);
    res.json({ read_only: true, linked: true, ...identity });
  } catch (err) {
    console.error('Web3 identity error:', err.message);
    res.status(502).json({ error: 'Identity unavailable' });
  }
});

// GET /api/web3/collectibles — NFTs split into metaverse worlds vs other.
router.get('/collectibles', async (req, res) => {
  try {
    const address = await walletAddressOf(req.user.user_id);
    if (!address) return res.json({ read_only: true, linked: false, available: false, reason: 'no_wallet' });
    const nft = await getWalletNfts(address);
    if (!nft || !nft.available) {
      return res.json({ read_only: true, linked: true, address, available: false, reason: (nft && nft.reason) || 'unavailable', note: nft && nft.note });
    }
    const { worlds, other, summary, world_count } = classifyWorlds(nft.items);
    res.json({
      read_only: true, linked: true, available: true,
      address: nft.address, chain: nft.chain, count: nft.count,
      worlds, other, summary, world_count,
      note: nft.note,
    });
  } catch (err) {
    console.error('Web3 collectibles error:', err.message);
    res.status(502).json({ error: 'Collectibles unavailable' });
  }
});

module.exports = router;
