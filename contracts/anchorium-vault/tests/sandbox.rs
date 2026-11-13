//! Sandbox integration test: the vault, the COMMITTED guard wasm (the deployed v1 guard, never rebuilt) and a mock
//! NEP-141 USDC (test-only, contracts/mock-ft) in a local NEAR sandbox.
//!
//! The agent signs `execute` ONLY with a function-call access key restricted to the vault's `execute` method.
use near_api::types::AccessKeyPermission;
use near_api::types::transaction::actions::FunctionCallPermission;
use near_api::{Account, AccountId, Contract, NearGas, NearToken, NetworkConfig, Signer, signer::generate_secret_key};
use near_sdk::serde_json::{Value, json};
use std::sync::Arc;

const GUARD_WASM: &str = "../anchorium-guard/res/anchorium_guard.wasm";
const MOCK_FT_WASM: &str = "../mock-ft/res/mock_ft.wasm";
const USDC: u128 = 1_000_000; // 6 decimals
const REGISTRATION: NearToken = NearToken::from_yoctonear(1_250_000_000_000_000_000_000);

struct World {
    sandbox: near_sandbox::Sandbox,
    network: NetworkConfig,
    genesis: Arc<Signer>,
    owner: AccountId,
    agent: AccountId,
    stranger: AccountId,
    guard: AccountId,
    vault: AccountId,
    usdc: AccountId,
    agent_fc: Arc<Signer>,
}

fn hash(n: u64) -> String {
    format!("{n:064x}")
}

/// 1Click deposit addresses are fresh implicit accounts (64 hex chars).
fn deposit(n: u64) -> String {
    format!("{:064x}", 0xdead_0000 + n)
}

async fn setup() -> testresult::TestResult<World> {
    let vault_wasm = std::fs::read(cargo_near_build::build_with_cli(Default::default())?)?;
    let guard_wasm = std::fs::read(GUARD_WASM)?;
    let ft_wasm = std::fs::read(MOCK_FT_WASM)?;
    let sandbox = near_sandbox::Sandbox::start_sandbox().await?;
    let network = NetworkConfig::from_rpc_url("sandbox", sandbox.rpc_addr.parse()?);
    let genesis = Signer::from_secret_key(near_sandbox::config::DEFAULT_GENESIS_ACCOUNT_PRIVATE_KEY.parse()?)?;

    let owner: AccountId = "owner.sandbox".parse()?;
    let guard: AccountId = "guard.sandbox".parse()?;
    let vault: AccountId = "vault.sandbox".parse()?;
    let usdc: AccountId = "usdc.sandbox".parse()?;
    let stranger: AccountId = "stranger.sandbox".parse()?;
    for id in [&owner, &guard, &vault, &usdc, &stranger] {
        sandbox.create_account(id.as_str().parse()?).initial_balance(NearToken::from_near(20)).send().await?;
    }
    let agent: AccountId = "agent.owner.sandbox".parse()?;
    Account::create_account(agent.clone())
        .fund_myself(owner.clone(), NearToken::from_near(2))
        .with_public_key(genesis.get_public_key().await?)
        .with_signer(genesis.clone())
        .send_to(&network)
        .await?
        .assert_success();
    // The agent's vault key: function calls to vault.execute only (no deposit, no other method, no transfers).
    let fc_secret = generate_secret_key()?;
    let agent_fc = Signer::from_secret_key(fc_secret.clone())?;
    Account(agent.clone())
        .add_key(
            AccessKeyPermission::FunctionCall(FunctionCallPermission {
                // Prepaid gas is charged against the allowance at the gas price: 1 NEAR covers many 200 Tgas calls.
                allowance: Some(NearToken::from_near(1)),
                receiver_id: vault.to_string(),
                method_names: vec!["execute".into()],
            }),
            fc_secret.public_key(),
        )
        .with_signer(genesis.clone())
        .send_to(&network)
        .await?
        .assert_success();

    Contract::deploy(usdc.clone())
        .use_code(ft_wasm)
        .with_init_call("new", json!({"owner": owner, "total_supply": (100 * USDC).to_string(), "symbol": "USDC"}))?
        .with_signer(genesis.clone())
        .send_to(&network)
        .await?
        .assert_success();
    Contract::deploy(guard.clone())
        .use_code(guard_wasm)
        .with_init_call("new", json!({"owner": owner, "agent": agent, "policy": {
            "allowed_assets": ["nep141:wrap.sandbox", "nep141:btc.sandbox", "nep141:eth.sandbox", "nep141:sol.sandbox"],
            "max_notional_per_trade_usd_cents": 200, "max_trades_per_day": 10, "max_daily_loss_usd_cents": 1000,
            "max_slippage_bps": 100, "cooldown_secs": 0, "max_open_positions": 4, "max_decisions_per_day": 50,
            "quote_asset": "nep141:usdc.sandbox", "quote_asset_decimals": 6, "paused": false}}))?
        .with_signer(genesis.clone())
        .send_to(&network)
        .await?
        .assert_success();
    Contract::deploy(vault.clone())
        .use_code(vault_wasm)
        .with_init_call("new", json!({"owner": owner, "agent": agent, "guard": guard, "quote_token": usdc,
            "quote_decimals": 6, "allowed_tokens": [usdc], "accept_dry_run": false, "max_releases_per_day": 5,
            "max_quote_per_day": (10 * USDC).to_string(),
            "max_storage_per_day": (5 * REGISTRATION.as_yoctonear()).to_string()}))?
        .with_signer(genesis.clone())
        .send_to(&network)
        .await?
        .assert_success();

    let w = World { sandbox, network, genesis, owner, agent, stranger, guard, vault, usdc, agent_fc };
    // Register the vault and the first deposit addresses on the token; the owner funds the vault with 20 USDC.
    for who in [w.vault.to_string(), deposit(1)] {
        w.call(&w.usdc, &w.owner, &w.genesis, "storage_deposit", json!({"account_id": who, "registration_only": true}),
               REGISTRATION).await.expect("storage_deposit");
    }
    w.call(&w.usdc, &w.owner, &w.genesis, "ft_transfer_call",
           json!({"receiver_id": w.vault, "amount": (20 * USDC).to_string(), "msg": ""}), NearToken::from_yoctonear(1))
        .await.expect("fund the vault");
    Ok(w)
}

impl World {
    async fn view(&self, contract: &AccountId, method: &str, args: Value) -> testresult::TestResult<Value> {
        Ok(Contract(contract.clone()).call_function(method, args).read_only().fetch_from(&self.network).await?.data)
    }

    async fn balance(&self, who: &str) -> u128 {
        let v = self.view(&self.usdc, "ft_balance_of", json!({"account_id": who})).await.unwrap();
        v.as_str().unwrap().parse().unwrap()
    }

    async fn call(&self, contract: &AccountId, signer_account: &AccountId, signer: &Arc<Signer>, method: &str, args: Value,
                  deposit: NearToken) -> Result<(Value, Vec<String>), String> {
        let sent = Contract(contract.clone())
            .call_function(method, args)
            .transaction()
            .gas(NearGas::from_tgas(200))
            .deposit(deposit)
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

    async fn decide(&self, n: u64, asset: &str, mode: &str, notional: u64, quote_id: &str) -> Value {
        let (d, _) = self.call(&self.guard, &self.agent, &self.genesis, "record_decision", json!({
            "signal_hash": hash(n), "asset": asset, "side": "BUY", "mode": mode, "notional_usd_cents": notional,
            "slippage_bps": 20, "quote_id": quote_id, "agent_allowed": true, "agent_reason": "OK", "rationale_hash": null
        }), NearToken::from_yoctonear(0)).await.expect("record_decision");
        d
    }

    /// The agent executes with its vault function-call key (no deposit).
    async fn execute(&self, id: u64, amount: u128, to: &str, quote_id: &str, register: bool)
        -> Result<(Value, Vec<String>), String> {
        self.call(&self.vault, &self.agent, &self.agent_fc, "execute", json!({
            "decision_id": id, "token": self.usdc, "amount": amount.to_string(), "deposit_address": to,
            "quote_id": quote_id, "register": register}), NearToken::from_yoctonear(0)).await
    }
}

fn has_event(logs: &[String], name: &str) -> bool {
    logs.iter().any(|l| l.starts_with("EVENT_JSON:") && l.contains(&format!("\"event\":\"{name}\"")))
}

fn rejected_with(logs: &[String], reason: &str) -> bool {
    logs.iter().any(|l| l.contains("\"event\":\"vault_release_rejected\"") && l.contains(reason))
}

#[tokio::test]
async fn the_vault_releases_funds_only_for_allowed_live_decisions() -> testresult::TestResult<()> {
    let w = setup().await?;
    assert_eq!(w.balance(w.vault.as_str()).await, 20 * USDC);
    let (d1_addr, d3_addr) = (deposit(1), deposit(3));
    let q0 = format!("q0:{d1_addr}");
    let q1 = format!("q1:{d1_addr}");
    let q3 = format!("q3:{d3_addr}");

    let d0 = w.decide(10, "nep141:wrap.sandbox", "LIVE", 499, &q0).await;   // guard: NOTIONAL_ABOVE_MAX
    assert_eq!(d0["allowed"], false);
    let d1 = w.decide(11, "nep141:wrap.sandbox", "LIVE", 200, &q1).await;
    let d2 = w.decide(12, "nep141:btc.sandbox", "DRY_RUN", 200, "q2").await;
    let d3 = w.decide(13, "nep141:eth.sandbox", "LIVE", 200, &q3).await;
    let (id0, id1, id2, id3) = (d0["id"].as_u64().unwrap(), d1["id"].as_u64().unwrap(), d2["id"].as_u64().unwrap(),
                                d3["id"].as_u64().unwrap());

    // (7) Only the agent: a stranger's call is refused by the contract.
    let stranger = w.call(&w.vault, &w.stranger, &w.genesis, "execute", json!({"decision_id": id1, "token": w.usdc,
        "amount": "1", "deposit_address": d1_addr, "quote_id": q1, "register": false}), NearToken::from_yoctonear(0)).await;
    assert!(stranger.unwrap_err().contains("ONLY_AGENT"));
    // The agent's key cannot reach the owner's methods at all (rejected by the protocol).
    let fc_withdraw = w.call(&w.vault, &w.agent, &w.agent_fc, "withdraw", json!({"token": w.usdc, "amount": "1"}),
                             NearToken::from_yoctonear(0)).await;
    assert!(fc_withdraw.unwrap_err().contains("InvalidTransaction"));

    // (3) A decision the guard REJECTED moves nothing.
    let (ok, logs) = w.execute(id0, 2 * USDC, &d1_addr, &q0, false).await.expect("tx succeeds, release refused");
    assert_eq!(ok, false);
    assert!(rejected_with(&logs, "NOT_ALLOWED"));
    // (5) More than the decision's notional is refused.
    let (_, logs) = w.execute(id1, 2 * USDC + 1, &d1_addr, &q1, false).await.unwrap();
    assert!(rejected_with(&logs, "AMOUNT_ABOVE_NOTIONAL"));
    // A deposit address other than the journaled one is refused.
    let (_, logs) = w.execute(id1, 2 * USDC, &deposit(9), &q1, false).await.unwrap();
    assert!(rejected_with(&logs, "DEPOSIT_ADDRESS_MISMATCH"));
    assert_eq!(w.balance(&d1_addr).await, 0);

    // (1) An ALLOWED LIVE decision: the vault pays exactly its notional to the journaled deposit address.
    let (_, logs) = w.execute(id1, 2 * USDC, &d1_addr, &q1, false).await.expect("release");
    assert!(has_event(&logs, "vault_release"));
    assert_eq!(w.balance(&d1_addr).await, 2 * USDC);
    assert_eq!(w.balance(w.vault.as_str()).await, 18 * USDC);
    assert_eq!(w.view(&w.vault, "is_executed", json!({"decision_id": id1})).await?, true);

    // (2) The same decision twice: refused.
    let again = w.execute(id1, 2 * USDC, &d1_addr, &q1, false).await;
    assert!(again.unwrap_err().contains("ALREADY_EXECUTED"));
    assert_eq!(w.balance(&d1_addr).await, 2 * USDC);

    // (4) A DRY_RUN decision on a production vault (accept_dry_run=false): refused.
    let (_, logs) = w.execute(id2, USDC, &deposit(2), "q2", false).await.unwrap();
    assert!(rejected_with(&logs, "DRY_RUN_DECISION"));

    // (8) The token transfer fails (deposit address not registered): the decision is un-marked, nothing moved.
    let (_, logs) = w.execute(id3, 2 * USDC, &d3_addr, &q3, false).await.expect("tx ok, transfer fails inside");
    assert!(has_event(&logs, "vault_release") && has_event(&logs, "release_failed"), "{logs:?}");
    assert_eq!(w.view(&w.vault, "is_executed", json!({"decision_id": id3})).await?, false);
    assert_eq!(w.balance(w.vault.as_str()).await, 18 * USDC);
    // ...so it can be retried, this time registering the address first (0.00125 NEAR from the vault).
    let (_, logs) = w.execute(id3, 2 * USDC, &d3_addr, &q3, true).await.expect("retry with registration");
    assert!(has_event(&logs, "vault_release") && !has_event(&logs, "release_failed"));
    assert_eq!(w.balance(&d3_addr).await, 2 * USDC);

    // (6) Paused: execute is refused; the owner can still withdraw.
    let d4 = w.decide(14, "nep141:sol.sandbox", "LIVE", 100, &format!("q4:{d1_addr}")).await;
    let (_, logs) = w.call(&w.vault, &w.owner, &w.genesis, "pause", json!({}), NearToken::from_yoctonear(0)).await.unwrap();
    assert!(has_event(&logs, "paused"));
    let paused = w.execute(d4["id"].as_u64().unwrap(), USDC, &d1_addr, &format!("q4:{d1_addr}"), false).await;
    assert!(paused.unwrap_err().contains("VAULT_PAUSED"));
    w.call(&w.usdc, &w.owner, &w.genesis, "storage_deposit", json!({"account_id": w.owner, "registration_only": true}),
           REGISTRATION).await.ok();
    let owner_before = w.balance(w.owner.as_str()).await;
    let (_, logs) = w.call(&w.vault, &w.owner, &w.genesis, "withdraw", json!({"token": w.usdc,
        "amount": USDC.to_string()}), NearToken::from_yoctonear(1)).await.expect("owner withdraw while paused");
    assert!(has_event(&logs, "withdraw"));
    assert_eq!(w.balance(w.owner.as_str()).await, owner_before + USDC);

    let daily = w.view(&w.vault, "get_daily", json!({})).await?;
    assert_eq!(daily["releases"], 2);
    assert_eq!(daily["quote_released"], (4 * USDC).to_string());
    drop(w.sandbox);
    Ok(())
}
