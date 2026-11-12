# YouTube upload (humans upload; never automated)

**Title:** Anchorium: Don't trust the bot. Verify it. | NEAR HACKATHON

**Visibility:** public or unlisted, no password.

**Description:**

Don't trust the bot. Verify it.

Anchorium is an autonomous trading agent whose risk limits live in a NEAR smart contract. Before any trade it asks
the contract, journals the decision on-chain, and settles spot swaps through NEAR Intents (1Click) into the user's
own account. In this silent, captioned video a simulated compromised agent asks for 2.5x its limit and the contract says no, on-chain.

Everything shown is a real run: testnet guard contract, real 1Click quotes, real transactions. Money scenes are
PAPER (real quotes, simulated fill); no live mainnet trade had run at recording time.

- Code: https://github.com/Adesh2204/anchorium-near
- Guard contract (NEAR testnet): https://testnet.nearblocks.io/address/guard.anchorium-team.testnet
- Verify it yourself: `git clone`, `make setup`, `make demo` (no keys, no funds)

Built at NEAR HACKATHON by Adesh & Parth. AI-assisted code: written with Claude Code.

**Chapters:**

```
0:00 The bot gets caught
0:13 The problem
0:27 How it works
0:47 A full cycle on-chain
1:24 Safety
1:58 Honest money
2:13 Verify it yourself
```
