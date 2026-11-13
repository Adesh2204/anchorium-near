# Final human checklist

Deadline: 2026-11-14 23:59 UTC (2026-11-15 05:29 IST). Internal target: 2026-11-14 20:00 IST.
Only humans submit the form and post on X. Keep the repo public through 18 Nov; don't push after the deadline.

## NEAR AI Cloud

- [ ] The NEAR AI key is only in `.env` (`NEAR_AI_API_KEY`), never pasted into a chat, never committed
- [ ] cloud.near.ai → Credits checked before and after any run. **Never add a card or top up**
- [ ] If hackathon credits arrive: `make paypal-demo` with the key, copy the signed receipts into
      `fixtures/near_ai/receipts/`, `make verify-ai` → all PASS, commit before the tag
- [ ] After judging: rotate the NEAR AI key (it was pasted into a chat on 13 Nov)

## Before tagging

- [ ] Mainnet (Phase 6): skipped, or approved by typing exactly `GO MAINNET`; README "Limitations" says which
- [ ] `make test` and `make vault-test` pass on the final commit; CI is green on GitHub
- [ ] A fresh clone passes the README judge steps (`make setup`, `make freeze`, `make demo`, `make status`,
      `make verify-ai`, `make paypal-demo`, `make dashboard`)
- [ ] Secret scan clean over the tree and the branch history (`bash scripts/secret_scan.sh`)

## Repository

- [ ] https://github.com/Adesh2204/anchorium-near-Paypal is **public** and stays public through judging
- [ ] GitHub repo description and topics set (e.g. `near`, `near-intents`, `near-ai`, `tee`, `smart-contracts`,
      `trading-agent`)
- [ ] `LICENSE` (MIT) is on `main`
- [ ] `main` contains the final commit (the `near-grand-prize` PR is merged)
- [ ] Tag `hackathon-submission` is on the final commit and pushed:
      `git tag hackathon-submission && git push origin hackathon-submission`
- [ ] https://github.com/Adesh2204/anchorium-near-Paypal/compare/baseline...hackathon-submission opens and shows the
      NEAR work

## Demo

- [ ] Video re-cut with the Vault and NEAR AI Cloud scenes (≤ 2:15) uploaded; README Demo link updated
- [ ] 60-second cut attached to the X post
- [ ] X post is live, says "Built at NEAR HACKATHON" and tags @NEARProtocol #NEARHackathon (`x_post.md`)
- [ ] The post URL replaces `X post: TODO_HUMAN` in README.md and `demoUrl` in `form.md`, committed **before** the tag

## Form (submitted once)

- [ ] Every `TODO_HUMAN` in `form.md` filled in: lead name, email, NEAR payout address, demo URL
- [ ] NEAR mainnet payout address double-checked (lowercase, an account you control)
- [ ] Tracks: `security`, `defi`, `privacy`, `open`
- [ ] Team size `2 - 4`
- [ ] Idea pasted from `form.md` (its character count is noted there; limit 2000)
- [ ] Submitted **once**. Any later correction (including an old repo URL) goes through
      https://nearhackathon.com/request-changes
