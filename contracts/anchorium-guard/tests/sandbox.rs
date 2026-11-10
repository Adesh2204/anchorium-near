//! Sandbox integration tests: the real wasm in a local NEAR sandbox (near-sandbox + near-api).
//!
//! The agent is a sub-account `agent.owner.sandbox` that signs ONLY with a function-call access key
//! restricted to the guard contract's two journaling methods, the same setup used on testnet.
use near_api::types::AccessKeyPermission;
use near_api::types::transaction::actions::FunctionCallPermission;
use near_api::{Account, AccountId, Contract, NearToken, NetworkConfig, Signer, Tokens, signer::generate_secret_key};
use near_sdk::serde_json::{Value, json};
use std::sync::Arc;

struct World {
    sandbox: near_sandbox::Sandbox,
    network: NetworkConfig,
    genesis: Arc<Signer>,
    owner: AccountId,
    agent: AccountId,
    stranger: AccountId,
    guard: AccountId,
    /// Signs as `agent` with the function-call key only.
    agent_fc: Arc<Signer>,
}

fn policy() -> Value {
    json!({
        "allowed_assets": ["nep141:wrap.near"],
        "max_notional_per_trade_usd_cents": 200,
        "max_trades_per_day": 5,
        "max_daily_loss_usd_cents": 50,
        "max_slippage_bps": 100,
        "cooldown_secs": 0,
        "max_open_positions": 1,
        "max_decisions_per_day": 50,
        "quote_asset": "nep141:usdc",
        "quote_asset_decimals": 6,
        "paused": false
    })
}

fn hash(n: u64) -> String {
    format!("{n:064x}")
}

async fn setup() -> testresult::TestResult<World> {
    let wasm = std::fs::read(cargo_near_build::build_with_cli(Default::default())?)?;
    let sandbox = near_sandbox::Sandbox::start_sandbox().await?;
    let network = NetworkConfig::from_rpc_url("sandbox", sandbox.rpc_addr.parse()?);
    let genesis = Signer::from_secret_key(near_sandbox::config::DEFAULT_GENESIS_ACCOUNT_PRIVATE_KEY.parse()?)?;

    let owner: AccountId = "owner.sandbox".parse()?;
    let guard: AccountId = "guard.sandbox".parse()?;
    let stranger: AccountId = "stranger.sandbox".parse()?;
    for id in [&owner, &guard, &stranger] {
        sandbox.create_account(id.as_str().parse()?).initial_balance(NearToken::from_near(20)).send().await?;
    }
    // agent.owner.sandbox: a sub-account created and funded by the owner.
    let agent: AccountId = "agent.owner.sandbox".parse()?;
    Account::create_account(agent.clone())
        .fund_myself(owner.clone(), NearToken::from_near(2))
        .with_public_key(genesis.get_public_key().await?)
        .with_signer(genesis.clone())
        .send_to(&network)
        .await?
        .assert_success();

    // The agent's operating key: function calls to the guard's journaling methods only.
    let fc_secret = generate_secret_key()?;
    let agent_fc = Signer::from_secret_key(fc_secret.clone())?;
    Account(agent.clone())
        .add_key(
            AccessKeyPermission::FunctionCall(FunctionCallPermission {
                allowance: Some(NearToken::from_millinear(250)),
                receiver_id: guard.to_string(),
                method_names: vec!["record_decision".into(), "record_outcome".into()],
            }),
            fc_secret.public_key(),
        )
        .with_signer(genesis.clone())
        .send_to(&network)
        .await?
        .assert_success();

    Contract::deploy(guard.clone())
        .use_code(wasm)
        .with_init_call("new", json!({"owner": owner, "agent": agent, "policy": policy()}))?
        .with_signer(genesis.clone())
        .send_to(&network)
        .await?
        .assert_success();

    Ok(World { sandbox, network, genesis, owner, agent, stranger, guard, agent_fc })
}

impl World {
    async fn view(&self, method: &str, args: Value) -> testresult::TestResult<Value> {
        Ok(Contract(self.guard.clone()).call_function(method, args).read_only().fetch_from(&self.network).await?.data)
    }

    /// Returns Ok(logs) on success, Err(message) when the transaction is rejected or its execution fails.
    async fn call(&self, signer_account: &AccountId, signer: &Arc<Signer>, method: &str, args: Value)
        -> Result<(Value, Vec<String>), String> {
        let sent = Contract(self.guard.clone())
            .call_function(method, args)
            .transaction()
            .with_signer(signer_account.clone(), signer.clone())
            .send_to(&self.network)
            .await;
        match sent {
            Err(e) => Err(format!("rejected: {e}")),
            Ok(result) => match result.into_result() {
                Ok(success) => {
                    let logs = success.logs().iter().map(|s| s.to_string()).collect();
                    Ok((success.json::<Value>().unwrap_or(Value::Null), logs))
                }
                Err(failure) => Err(format!("failed: {failure:?}")),
            },
        }
    }

    async fn agent_decide(&self, n: u64, side: &str, notional: u64) -> Result<(Value, Vec<String>), String> {
        self.call(&self.agent, &self.agent_fc, "record_decision", json!({
            "signal_hash": hash(n), "asset": "nep141:wrap.near", "side": side, "mode": "DRY_RUN",
            "notional_usd_cents": notional, "slippage_bps": 20, "quote_id": format!("quote-{n}"),
            "agent_allowed": true, "agent_reason": "OK", "rationale_hash": null
        })).await
    }

    async fn agent_outcome(&self, id: u64, amount_in: &str, amount_out: &str) -> Result<(Value, Vec<String>), String> {
        self.call(&self.agent, &self.agent_fc, "record_outcome", json!({
            "decision_id": id, "status": "DRY_RUN", "amount_in": amount_in, "amount_out": amount_out,
            "tx_ref": format!("dry-{id}")
        })).await
    }
}

fn has_event(logs: &[String], name: &str) -> bool {
    logs.iter().any(|l| l.starts_with("EVENT_JSON:") && l.contains(&format!("\"event\":\"{name}\"")))
}

#[tokio::test]
async fn function_call_key_can_journal_but_nothing_else() -> testresult::TestResult<()> {
    let w = setup().await?;
    // Allowed: record_decision with the restricted key.
    let (decision, logs) = w.agent_decide(1, "BUY", 200).await.expect("FC key may journal");
    assert_eq!(decision["allowed"], true);
    assert!(has_event(&logs, "decision_recorded"));

    // The same key cannot call any other method, even one it might want (pause / update_policy).
    let paused = w.call(&w.agent, &w.agent_fc, "pause", json!({})).await;
    // Rejected by the protocol at validation (method not in the key's allow-list), not by the contract.
    assert!(paused.as_ref().unwrap_err().contains("InvalidTransaction"), "FC key must not reach pause: {paused:?}");
    let patched = w.call(&w.agent, &w.agent_fc, "update_policy",
        json!({"patch": {"max_notional_per_trade_usd_cents": 100000000}})).await;
    assert!(patched.as_ref().unwrap_err().contains("InvalidTransaction"), "FC key must not reach update_policy: {patched:?}");

    // And it cannot move the agent account's NEAR.
    let transfer = Tokens::account(w.agent.clone())
        .send_to(w.stranger.clone())
        .near(NearToken::from_millinear(100))
        .with_signer(w.agent_fc.clone())
        .send_to(&w.network)
        .await;
    // A transfer needs a full-access key: also rejected at validation.
    assert!(transfer.err().map(|e| e.to_string()).unwrap_or_default().contains("InvalidTransaction"),
        "FC key must not transfer NEAR");

    // Contract-level auth: even a full-access key of the agent account is only the agent, not the owner.
    let as_agent = w.call(&w.agent, &w.genesis, "pause", json!({})).await;
    assert!(as_agent.unwrap_err().contains("ONLY_OWNER"));
    let stranger = w.call(&w.stranger, &w.genesis, "record_decision", json!({
        "signal_hash": hash(9), "asset": "nep141:wrap.near", "side": "BUY", "mode": "DRY_RUN",
        "notional_usd_cents": 100, "slippage_bps": 0, "quote_id": "q", "agent_allowed": true,
        "agent_reason": "OK", "rationale_hash": null})).await;
    assert!(stranger.unwrap_err().contains("ONLY_AGENT"));
    drop(w.sandbox);
    Ok(())
}

#[tokio::test]
async fn guard_rules_kill_switch_and_auto_pause_on_chain() -> testresult::TestResult<()> {
    let w = setup().await?;
    assert_eq!(w.view("get_policy", json!({})).await?["max_notional_per_trade_usd_cents"], 200);
    assert_eq!(w.view("get_roles", json!({})).await?["agent"], w.agent.as_str());

    // View check, then an over-limit decision the agent claims is allowed: recorded as rejected.
    let verdict = w.view("check_trade", json!({"asset": "nep141:wrap.near", "side": "BUY",
        "notional_usd_cents": 10_000, "slippage_bps": null})).await?;
    assert_eq!(verdict, json!({"allowed": false, "reason": "NOTIONAL_ABOVE_MAX"}));
    let (over, logs) = w.agent_decide(1, "BUY", 10_000).await.unwrap();
    assert_eq!(over["allowed"], false);
    assert_eq!(over["reason"], "NOTIONAL_ABOVE_MAX");
    assert!(has_event(&logs, "verdict_override"));

    // Idempotent replay of the same signal hash returns the same decision.
    let (again, _) = w.agent_decide(1, "BUY", 50).await.unwrap();
    assert_eq!(again, over);

    // Kill switch: owner pauses, entries are rejected, owner resumes.
    let (_, logs) = w.call(&w.owner, &w.genesis, "pause", json!({})).await.unwrap();
    assert!(has_event(&logs, "paused"));
    let (blocked, _) = w.agent_decide(2, "BUY", 100).await.unwrap();
    assert_eq!(blocked["reason"], "PAUSED");
    w.call(&w.owner, &w.genesis, "resume", json!({})).await.unwrap();

    // A losing paper round trip: $2.00 in, $1.40 out = -60 cents >= 50 cent cap -> auto-pause.
    let (buy, _) = w.agent_decide(3, "BUY", 200).await.unwrap();
    assert_eq!(buy["allowed"], true);
    w.agent_outcome(buy["id"].as_u64().unwrap(), "2000000", "540000000000000000000000").await.unwrap();
    assert_eq!(w.view("get_positions", json!({})).await?[0]["state"], "OPEN");
    let (sell, _) = w.agent_decide(4, "SELL", 140).await.unwrap();
    assert_eq!(sell["allowed"], true);
    let (closed, logs) = w.agent_outcome(sell["id"].as_u64().unwrap(), "540000000000000000000000", "1400000")
        .await.unwrap();
    assert_eq!(closed["outcome"]["realized_pnl_usd_cents"], -60);
    assert!(has_event(&logs, "auto_paused"));

    let stats = w.view("get_stats", json!({})).await?;
    assert_eq!(stats["paused"], true);
    assert_eq!(stats["dry_run"]["closed_trades"], 1);
    assert_eq!(stats["dry_run"]["losses"], 1);
    assert_eq!(stats["dry_run"]["realized_pnl_usd_cents"], -60);
    assert_eq!(stats["live"]["decisions"], 0);
    let page = w.view("get_decisions", json!({"from_index": 0, "limit": 10})).await?;
    assert_eq!(page.as_array().unwrap().len(), 4);

    // Storage: each journaled decision costs well under 1 KB of contract storage.
    let before = Account(w.guard.clone()).view().fetch_from(&w.network).await?.data.storage_usage;
    w.call(&w.owner, &w.genesis, "resume", json!({})).await.unwrap();
    w.agent_decide(5, "BUY", 100).await.unwrap();
    let after = Account(w.guard.clone()).view().fetch_from(&w.network).await?.data.storage_usage;
    assert!(after - before < 1024, "decision storage {} bytes", after - before);
    drop(w.sandbox);
    Ok(())
}
