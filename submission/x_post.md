# X post draft (a human posts it; nothing has been posted)

Attach the 90-second screen recording (see `video_script.md`). After posting, put the post URL into
`README.md` (`TODO_DEMO_URL`) and `form.md` (`demoUrl`).

## Main post

> Don't trust the bot. Verify it. We moved a trading bot's risk rules into a NEAR contract.
>
> Our agent asked for a $4.99 trade and claimed it was allowed. The guard contract said no (cap $2), recorded the
> rejection on-chain and flagged the override.
>
> Built at NEAR HACKATHON @NEARProtocol #NEARHackathon
>
> [video]

Length: 310 characters (counted with `len()`), no link. If the account cannot post more than 280
characters, use the shorter version (229 characters):

> Don't trust the bot. Verify it. We moved the rules into a NEAR contract: our agent
> asked for $4.99 and claimed "allowed". The contract said no (cap $2) and logged it on-chain.
>
> Built at NEAR HACKATHON @NEARProtocol #NEARHackathon

## Reply (thread, 274 characters by `len()`)

> How NEAR is used:
> • guard contract: policy, on-chain journal, auto-pause at the loss cap
> • agent key can only journal, not move funds
> • swaps via NEAR Intents 1Click into your own account
>
> No keys needed: make demo
> github.com/Adesh2204/anchorium-near
>
> Next: a mainnet vault.

Structure (as required): user problem → one complete action with a visible result (the rejected $4.99 trade)
→ NEAR's concrete role → next step. Before posting, count the characters again in the X composer: it counts
links and emoji its own way.
