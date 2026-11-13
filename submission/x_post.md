# X post draft (a human posts it; nothing has been posted)

Attach the 60-second cut (`submission/media/out/anchorium_demo_x.mp4` once Phase 7 renders it). After posting,
put the post URL into `README.md` (Demo, `X post: TODO_HUMAN`) and `form.md` (`demoUrl`), and commit that before
the tag.

## Main post

> Would you hand a trading bot your money and trust it to follow its own rules?
>
> We don't. Our agent asked to spend $4.99 and said "allowed". The NEAR guard contract said no (cap $2) and
> logged it on-chain. Our NEAR Vault paid nothing for that decision, and exactly $2 for an allowed one.
>
> Built at NEAR HACKATHON @NEARProtocol #NEARHackathon
>
> [video]

## Reply (thread)

> How NEAR is used:
> • guard contract: policy, on-chain journal, P&L, auto-pause
> • Vault: releases funds only for an ALLOWED decision
> • NEAR AI Cloud: TEE-signed AI reviews, verify offline
> • NEAR Intents 1Click for swaps
>
> Try it, keyless: make demo && make verify-ai
> github.com/Adesh2204/anchorium-near-Paypal
>
> Feedback welcome: what would you want a bot to prove?

Lengths by `len()`: main post 349, reply 361 (fine for accounts that may post more than 280 characters).

## Short versions (≤ 280 characters by `len()`)

Main (279):

> Would you trust a trading bot to follow its own rules?
>
> Our agent asked to spend $4.99 and said "allowed". The NEAR guard contract said no (cap $2) and logged it. Our NEAR Vault paid nothing for it, and $2 for an allowed one.
>
> Built at NEAR HACKATHON @NEARProtocol #NEARHackathon

Reply (278):

> How NEAR is used:
> • guard: policy, journal, P&L, auto-pause
> • Vault: pays only ALLOWED decisions
> • NEAR AI Cloud: TEE-signed reviews
> • Intents 1Click swaps
>
> Keyless: make demo && make verify-ai
> github.com/Adesh2204/anchorium-near-Paypal
> What should a bot prove? Feedback welcome

Structure (as required): user problem → one complete action with a visible result (the rejected $4.99 decision,
the vault paying nothing, then exactly $2 for an allowed one) → NEAR's role (guard, Vault, NEAR AI Cloud,
Intents) → an invitation for feedback, the repo link and the video. Count the characters in the X composer before
posting (it counts links its own way); the testnet vault paid a TEST-ONLY token, so keep "$2" tied to the decision,
as written, rather than claiming real dollars moved.
