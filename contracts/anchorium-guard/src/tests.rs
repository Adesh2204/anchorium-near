//! Unit tests: every public method, every verdict reason, the kill switch, auto-pause and caller checks.
use super::*;
use near_sdk::test_utils::{VMContextBuilder, get_logs};
use near_sdk::testing_env;

const USDC: &str = "nep141:usdc";
const NEAR: &str = "nep141:wrap.near";
const BTC: &str = "nep141:nbtc.bridge.near";
const T0: u64 = 1_762_732_800_000; // 2025-11-10 00:00:00 UTC in ms (a day boundary)

fn owner() -> AccountId { "owner.testnet".parse().unwrap() }
fn agent() -> AccountId { "agent.owner.testnet".parse().unwrap() }
fn stranger() -> AccountId { "mallory.testnet".parse().unwrap() }
fn guard() -> AccountId { "guard.owner.testnet".parse().unwrap() }

fn policy() -> Policy {
    Policy {
        allowed_assets: vec![NEAR.into(), BTC.into()],
        max_notional_per_trade_usd_cents: 200,
        max_trades_per_day: 3,
        max_daily_loss_usd_cents: 100,
        max_slippage_bps: 100,
        cooldown_secs: 60,
        max_open_positions: 1,
        max_decisions_per_day: 20,
        quote_asset: USDC.into(),
        quote_asset_decimals: 6,
        paused: false,
    }
}

fn ctx(caller: AccountId, now_ms: u64) {
    testing_env!(VMContextBuilder::new()
        .current_account_id(guard())
        .predecessor_account_id(caller)
        .block_timestamp(now_ms * 1_000_000)
        .build());
}

fn setup() -> Contract {
    ctx(guard(), T0);
    Contract::new(owner(), agent(), policy())
}

fn hash(n: u64) -> String { format!("{n:064x}") }

/// Journals a decision as the agent at `now_ms`, with the agent's own verdict "allowed".
fn decide(c: &mut Contract, n: u64, asset: &str, side: Side, notional: u64, now_ms: u64) -> Decision {
    ctx(agent(), now_ms);
    c.record_decision(hash(n), asset.into(), side, Mode::DryRun, notional, 10, format!("q{n}"), true, "OK".into(), None)
}

fn outcome(c: &mut Contract, id: u32, status: OutcomeStatus, amount_in: u128, amount_out: u128, now_ms: u64) -> Decision {
    ctx(agent(), now_ms);
    c.record_outcome(id, status, U128(amount_in), U128(amount_out), format!("tx{id}"))
}

/// Buy $2.00 of NEAR (USDC 6 decimals), then sell for `proceeds_usdc_units`.
fn round_trip(c: &mut Contract, n: u64, now_ms: u64, proceeds_usdc_units: u128) -> Decision {
    let buy = decide(c, n, NEAR, Side::Buy, 200, now_ms);
    assert!(buy.allowed, "{:?}", buy.reason);
    outcome(c, buy.id, OutcomeStatus::DryRun, 2_000_000, 540_000_000_000_000_000_000_000, now_ms);
    let sell = decide(c, n + 1000, NEAR, Side::Sell, 200, now_ms + 1);
    assert!(sell.allowed, "{:?}", sell.reason);
    outcome(c, sell.id, OutcomeStatus::DryRun, 540_000_000_000_000_000_000_000, proceeds_usdc_units, now_ms + 1)
}

fn events(name: &str) -> usize {
    get_logs().iter().filter(|l| l.starts_with("EVENT_JSON:") && l.contains(&format!("\"event\":\"{name}\""))).count()
}

// ----- init -----

#[test]
fn new_stores_roles_and_policy_and_emits_nep297() {
    let c = setup();
    assert_eq!(c.get_roles(), Roles { owner: owner(), agent: agent() });
    assert_eq!(c.get_policy(), policy());
    let log = &get_logs()[0];
    assert!(log.starts_with("EVENT_JSON:"));
    let v: serde_json::Value = serde_json::from_str(&log["EVENT_JSON:".len()..]).unwrap();
    assert_eq!(v["standard"], EVENT_STANDARD);
    assert_eq!(v["version"], EVENT_VERSION);
    assert_eq!(v["event"], "initialized");
}

#[test]
#[should_panic(expected = "OWNER_AND_AGENT_MUST_DIFFER")]
fn new_rejects_owner_equal_agent() {
    ctx(guard(), T0);
    Contract::new(owner(), owner(), policy());
}

#[test]
#[should_panic(expected = "POLICY_SLIPPAGE_ABOVE_100_PERCENT")]
fn new_rejects_invalid_policy() {
    ctx(guard(), T0);
    Contract::new(owner(), agent(), Policy { max_slippage_bps: 10_001, ..policy() });
}

#[test]
#[should_panic(expected = "POLICY_ASSET_DUPLICATE")]
fn policy_rejects_duplicate_assets() {
    ctx(guard(), T0);
    Contract::new(owner(), agent(), Policy { allowed_assets: vec![NEAR.into(), NEAR.into()], ..policy() });
}

#[test]
#[should_panic(expected = "POLICY_MAX_DECISIONS_OUT_OF_RANGE")]
fn policy_caps_decisions_per_day() {
    ctx(guard(), T0);
    Contract::new(owner(), agent(), Policy { max_decisions_per_day: MAX_DECISIONS_PER_DAY_CAP + 1, ..policy() });
}

// ----- owner methods -----

#[test]
fn owner_updates_policy_with_patch() {
    let mut c = setup();
    ctx(owner(), T0);
    let p = c.update_policy(PolicyPatch { max_trades_per_day: Some(5), cooldown_secs: Some(0), ..Default::default() });
    assert_eq!(p.max_trades_per_day, 5);
    assert_eq!(p.cooldown_secs, 0);
    assert_eq!(p.max_notional_per_trade_usd_cents, 200, "untouched fields stay");
    assert_eq!(events("policy_updated"), 1);
}

#[test]
#[should_panic(expected = "POLICY_MAX_NOTIONAL_ZERO")]
fn update_policy_validates() {
    let mut c = setup();
    ctx(owner(), T0);
    c.update_policy(PolicyPatch { max_notional_per_trade_usd_cents: Some(0), ..Default::default() });
}

#[test]
#[should_panic(expected = "ONLY_OWNER")]
fn agent_cannot_update_policy() {
    let mut c = setup();
    ctx(agent(), T0);
    c.update_policy(PolicyPatch { max_notional_per_trade_usd_cents: Some(1_000_000), ..Default::default() });
}

#[test]
#[should_panic(expected = "ONLY_OWNER")]
fn stranger_cannot_pause() {
    let mut c = setup();
    ctx(stranger(), T0);
    c.pause();
}

#[test]
#[should_panic(expected = "ONLY_OWNER")]
fn agent_cannot_resume() {
    let mut c = setup();
    ctx(agent(), T0);
    c.resume();
}

#[test]
fn owner_can_replace_agent_and_old_agent_loses_access() {
    let mut c = setup();
    ctx(owner(), T0);
    c.set_agent(stranger());
    assert_eq!(c.get_roles().agent, stranger());
    assert_eq!(events("agent_changed"), 1);
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        decide(&mut c, 1, NEAR, Side::Buy, 100, T0);
    }));
    assert!(result.is_err(), "old agent must be rejected");
}

#[test]
#[should_panic(expected = "OWNER_AND_AGENT_MUST_DIFFER")]
fn set_agent_rejects_owner() {
    let mut c = setup();
    ctx(owner(), T0);
    c.set_agent(owner());
}

// ----- kill switch -----

#[test]
fn pause_blocks_entries_but_not_exits_and_resume_restores() {
    let mut c = setup();
    let buy = decide(&mut c, 1, NEAR, Side::Buy, 200, T0);
    outcome(&mut c, buy.id, OutcomeStatus::DryRun, 2_000_000, 5, T0);
    ctx(owner(), T0);
    c.pause();
    assert!(c.get_policy().paused);
    assert_eq!(c.check_trade(BTC.into(), Side::Buy, 100, None).reason, Reason::Paused);
    let blocked = decide(&mut c, 2, BTC, Side::Buy, 100, T0 + 120_000);
    assert!(!blocked.allowed);
    assert_eq!(blocked.reason, Reason::Paused);
    let exit = decide(&mut c, 3, NEAR, Side::Sell, 200, T0 + 120_000);
    assert!(exit.allowed, "exits stay possible while paused: {:?}", exit.reason);
    ctx(owner(), T0);
    c.resume();
    assert!(!c.get_policy().paused);
    assert_eq!(events("resumed"), 1);
}

// ----- every BUY verdict reason -----

#[test]
fn check_trade_reports_each_reason() {
    let mut c = setup();
    ctx(stranger(), T0); // check_trade is a view: anyone may call it
    assert_eq!(c.check_trade(NEAR.into(), Side::Buy, 200, Some(100)), Verdict { allowed: true, reason: Reason::Ok });
    assert_eq!(c.check_trade(NEAR.into(), Side::Buy, 0, None).reason, Reason::InvalidNotional);
    assert_eq!(c.check_trade(NEAR.into(), Side::Buy, 201, None).reason, Reason::NotionalAboveMax);
    assert_eq!(c.check_trade(NEAR.into(), Side::Buy, 100, Some(101)).reason, Reason::SlippageAboveMax);
    assert_eq!(c.check_trade("nep141:evil".into(), Side::Buy, 100, None).reason, Reason::AssetNotAllowed);
    assert_eq!(c.check_trade(NEAR.into(), Side::Sell, 100, None).reason, Reason::NoOpenPosition);

    decide(&mut c, 1, NEAR, Side::Buy, 200, T0);
    ctx(stranger(), T0);
    assert_eq!(c.check_trade(NEAR.into(), Side::Buy, 100, None).reason, Reason::PositionAlreadyOpen);
    assert_eq!(c.check_trade(BTC.into(), Side::Buy, 100, None).reason, Reason::MaxOpenPositions);
    assert_eq!(c.check_trade(NEAR.into(), Side::Sell, 100, None).reason, Reason::NoOpenPosition, "entry still pending");
}

#[test]
fn cooldown_blocks_second_entry_until_elapsed() {
    let mut c = setup();
    round_trip(&mut c, 1, T0, 2_000_000);
    let early = decide(&mut c, 2, BTC, Side::Buy, 100, T0 + 59_000);
    assert_eq!(early.reason, Reason::Cooldown);
    let later = decide(&mut c, 3, BTC, Side::Buy, 100, T0 + 60_000);
    assert!(later.allowed, "{:?}", later.reason);
}

#[test]
fn daily_trade_limit_resets_next_utc_day() {
    let mut c = setup();
    ctx(owner(), T0);
    c.update_policy(PolicyPatch { cooldown_secs: Some(0), ..Default::default() });
    for i in 0..3u64 {
        round_trip(&mut c, 10 + i, T0 + i * 10, 2_000_000);
    }
    let blocked = decide(&mut c, 20, NEAR, Side::Buy, 100, T0 + 100);
    assert_eq!(blocked.reason, Reason::DailyTradeLimit);
    let next_day = decide(&mut c, 21, NEAR, Side::Buy, 100, T0 + MS_PER_DAY);
    assert!(next_day.allowed, "{:?}", next_day.reason);
    assert_eq!(c.get_stats().today.entries, 1);
}

// ----- record_decision enforcement -----

#[test]
fn contract_overrides_an_agent_that_claims_allowed() {
    let mut c = setup();
    let d = decide(&mut c, 1, NEAR, Side::Buy, 10_000, T0); // $100 > $2 cap, agent says allowed
    assert!(!d.allowed);
    assert!(d.agent_allowed);
    assert_eq!(d.reason, Reason::NotionalAboveMax);
    assert_eq!(events("verdict_override"), 1);
    assert_eq!(c.get_stats().dry_run.verdict_overrides, 1);
    assert!(c.get_positions().is_empty(), "a rejected decision opens nothing");
}

#[test]
fn agent_rejection_is_final_even_when_contract_allows() {
    let mut c = setup();
    ctx(agent(), T0);
    let d = c.record_decision(hash(1), NEAR.into(), Side::Buy, Mode::DryRun, 100, 10, "q".into(), false,
        "QUOTE_FAILED".into(), None);
    assert!(!d.allowed);
    assert_eq!(d.reason, Reason::AgentRejected);
    assert_eq!(d.agent_reason, "QUOTE_FAILED");
    assert_eq!(c.get_stats().today.entries, 0);
}

#[test]
fn slippage_is_enforced_on_record() {
    let mut c = setup();
    ctx(agent(), T0);
    let d = c.record_decision(hash(1), NEAR.into(), Side::Buy, Mode::DryRun, 100, 150, "q".into(), true,
        "OK".into(), None);
    assert_eq!(d.reason, Reason::SlippageAboveMax);
}

#[test]
fn record_decision_is_idempotent_per_signal_hash() {
    let mut c = setup();
    let first = decide(&mut c, 7, NEAR, Side::Buy, 200, T0);
    let again = decide(&mut c, 7, BTC, Side::Buy, 1, T0 + 5_000);
    assert_eq!(first, again, "same signal hash returns the original decision unchanged");
    assert_eq!(c.get_stats().total_decisions, 1);
    assert_eq!(c.get_stats().today.decisions, 1);
}

#[test]
#[should_panic(expected = "ONLY_AGENT")]
fn stranger_cannot_record_decision() {
    let mut c = setup();
    ctx(stranger(), T0);
    c.record_decision(hash(1), NEAR.into(), Side::Buy, Mode::DryRun, 100, 0, "q".into(), true, "OK".into(), None);
}

#[test]
#[should_panic(expected = "ONLY_AGENT")]
fn owner_cannot_record_decision() {
    let mut c = setup();
    ctx(owner(), T0);
    c.record_decision(hash(1), NEAR.into(), Side::Buy, Mode::DryRun, 100, 0, "q".into(), true, "OK".into(), None);
}

#[test]
#[should_panic(expected = "SIGNAL_HASH_MUST_BE_64_LOWERCASE_HEX")]
fn signal_hash_must_be_hex64() {
    let mut c = setup();
    ctx(agent(), T0);
    c.record_decision("abc".into(), NEAR.into(), Side::Buy, Mode::DryRun, 100, 0, "q".into(), true, "OK".into(), None);
}

#[test]
#[should_panic(expected = "QUOTE_ID_INVALID")]
fn oversized_text_is_rejected() {
    let mut c = setup();
    ctx(agent(), T0);
    c.record_decision(hash(1), NEAR.into(), Side::Buy, Mode::DryRun, 100, 0, "q".repeat(MAX_TEXT_LEN + 1), true,
        "OK".into(), None);
}

#[test]
#[should_panic(expected = "RATIONALE_HASH_MUST_BE_64_LOWERCASE_HEX")]
fn rationale_hash_must_be_hex64() {
    let mut c = setup();
    ctx(agent(), T0);
    c.record_decision(hash(1), NEAR.into(), Side::Buy, Mode::DryRun, 100, 0, "q".into(), true, "OK".into(),
        Some("nothex".into()));
}

#[test]
fn rationale_hash_is_stored() {
    let mut c = setup();
    ctx(agent(), T0);
    let d = c.record_decision(hash(1), NEAR.into(), Side::Buy, Mode::DryRun, 100, 0, "q".into(), true, "OK".into(),
        Some(hash(99)));
    assert_eq!(c.get_decision(d.id).unwrap().rationale_hash, Some(hash(99)));
}

#[test]
fn daily_decision_cap_stops_storage_spam() {
    let mut c = setup();
    ctx(owner(), T0);
    c.update_policy(PolicyPatch { max_decisions_per_day: Some(2), ..Default::default() });
    decide(&mut c, 1, "nep141:evil", Side::Buy, 100, T0);
    decide(&mut c, 2, "nep141:evil", Side::Buy, 100, T0);
    let third = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        decide(&mut c, 3, "nep141:evil", Side::Buy, 100, T0);
    }));
    assert!(third.is_err());
    decide(&mut c, 4, "nep141:evil", Side::Buy, 100, T0 + MS_PER_DAY); // next day works again
}

// ----- record_outcome -----

#[test]
fn round_trip_computes_pnl_in_the_contract() {
    let mut c = setup();
    let sell = round_trip(&mut c, 1, T0, 2_150_000); // $2.00 in, $2.15 out
    assert_eq!(sell.outcome.as_ref().unwrap().realized_pnl_usd_cents, Some(15));
    let s = c.get_stats();
    assert_eq!(s.dry_run.closed_trades, 1);
    assert_eq!(s.dry_run.wins, 1);
    assert_eq!(s.dry_run.realized_pnl_usd_cents, 15);
    assert_eq!(s.live, Book::default(), "paper results never touch the live book");
    assert!(c.get_positions().is_empty());
}

#[test]
fn drawdown_tracks_peak_to_trough() {
    let mut c = setup();
    ctx(owner(), T0);
    c.update_policy(PolicyPatch { cooldown_secs: Some(0), max_daily_loss_usd_cents: Some(10_000), ..Default::default() });
    round_trip(&mut c, 1, T0, 2_300_000); // +30
    round_trip(&mut c, 2, T0 + 10, 1_800_000); // -20
    round_trip(&mut c, 3, T0 + 20, 1_900_000); // -10
    let b = c.get_stats().dry_run;
    assert_eq!((b.wins, b.losses), (1, 2));
    assert_eq!(b.realized_pnl_usd_cents, 0);
    assert_eq!(b.peak_pnl_usd_cents, 30);
    assert_eq!(b.max_drawdown_usd_cents, 30);
}

#[test]
fn daily_loss_cap_auto_pauses_and_blocks_until_next_day() {
    let mut c = setup();
    round_trip(&mut c, 1, T0, 1_000_000); // -$1.00 == cap
    assert!(c.get_policy().paused, "auto-paused");
    assert_eq!(events("auto_paused"), 1);
    ctx(owner(), T0);
    c.resume();
    assert_eq!(c.check_trade(NEAR.into(), Side::Buy, 100, None).reason, Reason::DailyLossLimit);
    ctx(agent(), T0 + MS_PER_DAY);
    assert!(c.check_trade(NEAR.into(), Side::Buy, 100, None).allowed, "new UTC day");
}

#[test]
fn refunded_entry_frees_the_slot() {
    let mut c = setup();
    ctx(agent(), T0);
    let buy = c.record_decision(hash(1), NEAR.into(), Side::Buy, Mode::Live, 200, 10, "q".into(), true, "OK".into(), None);
    let d = outcome(&mut c, buy.id, OutcomeStatus::Refunded, 2_000_000, 0, T0);
    assert_eq!(d.outcome.unwrap().status, OutcomeStatus::Refunded);
    assert!(c.get_positions().is_empty());
    assert_eq!(c.get_stats().live.refunded, 1);
}

#[test]
fn failed_exit_keeps_position_open_for_a_retry() {
    let mut c = setup();
    ctx(agent(), T0);
    let buy = c.record_decision(hash(1), NEAR.into(), Side::Buy, Mode::Live, 200, 10, "q".into(), true, "OK".into(), None);
    outcome(&mut c, buy.id, OutcomeStatus::Success, 2_000_000, 5, T0);
    ctx(agent(), T0 + 1);
    let sell = c.record_decision(hash(2), NEAR.into(), Side::Sell, Mode::Live, 200, 10, "q".into(), true, "OK".into(), None);
    ctx(agent(), T0 + 1);
    assert_eq!(c.check_trade(NEAR.into(), Side::Sell, 200, None).reason, Reason::ExitPending);
    outcome(&mut c, sell.id, OutcomeStatus::Failed, 5, 0, T0 + 2);
    assert_eq!(c.get_positions().len(), 1);
    assert_eq!(c.get_positions()[0].exit_decision_id, None);
    assert_eq!(c.get_stats().live.failed, 1);
    ctx(agent(), T0 + 3);
    assert!(c.check_trade(NEAR.into(), Side::Sell, 200, None).allowed);
}

#[test]
fn outcome_is_idempotent_but_cannot_be_changed() {
    let mut c = setup();
    let buy = decide(&mut c, 1, NEAR, Side::Buy, 200, T0);
    let a = outcome(&mut c, buy.id, OutcomeStatus::DryRun, 2_000_000, 5, T0);
    let b = outcome(&mut c, buy.id, OutcomeStatus::DryRun, 2_000_000, 5, T0 + 1);
    assert_eq!(a, b);
    assert_eq!(c.get_stats().dry_run.settled, 1);
    let changed = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        outcome(&mut c, buy.id, OutcomeStatus::DryRun, 1, 5, T0 + 2);
    }));
    assert!(changed.is_err());
}

#[test]
#[should_panic(expected = "STATUS_MODE_MISMATCH")]
fn dry_run_decision_cannot_claim_live_success() {
    let mut c = setup();
    let buy = decide(&mut c, 1, NEAR, Side::Buy, 200, T0);
    outcome(&mut c, buy.id, OutcomeStatus::Success, 2_000_000, 5, T0);
}

#[test]
#[should_panic(expected = "STATUS_MODE_MISMATCH")]
fn live_decision_cannot_claim_dry_run() {
    let mut c = setup();
    ctx(agent(), T0);
    let buy = c.record_decision(hash(1), NEAR.into(), Side::Buy, Mode::Live, 200, 10, "q".into(), true, "OK".into(), None);
    outcome(&mut c, buy.id, OutcomeStatus::DryRun, 2_000_000, 5, T0);
}

#[test]
#[should_panic(expected = "DECISION_NOT_ALLOWED")]
fn rejected_decision_takes_no_outcome() {
    let mut c = setup();
    let d = decide(&mut c, 1, NEAR, Side::Buy, 10_000, T0);
    outcome(&mut c, d.id, OutcomeStatus::DryRun, 1, 1, T0);
}

#[test]
#[should_panic(expected = "UNKNOWN_DECISION")]
fn unknown_decision_id() {
    let mut c = setup();
    outcome(&mut c, 42, OutcomeStatus::DryRun, 1, 1, T0);
}

#[test]
#[should_panic(expected = "ONLY_AGENT")]
fn stranger_cannot_record_outcome() {
    let mut c = setup();
    let buy = decide(&mut c, 1, NEAR, Side::Buy, 200, T0);
    ctx(stranger(), T0);
    c.record_outcome(buy.id, OutcomeStatus::DryRun, U128(1), U128(1), "tx".into());
}

// ----- views -----

#[test]
fn get_decisions_pages_and_caps_limit() {
    let mut c = setup();
    for i in 0..5u64 {
        decide(&mut c, i + 1, "nep141:evil", Side::Buy, 100, T0);
    }
    let page = c.get_decisions(1, 2);
    assert_eq!(page.iter().map(|d| d.id).collect::<Vec<_>>(), vec![1, 2]);
    assert_eq!(c.get_decisions(4, 100).len(), 1);
    assert!(c.get_decisions(10, 5).is_empty());
    assert!(c.get_decision(99).is_none());
}

#[test]
fn quote_units_to_cents_rounds_down() {
    assert_eq!(quote_units_to_cents(2_000_000, 6), 200);
    assert_eq!(quote_units_to_cents(1_999_999, 6), 199);
    assert_eq!(quote_units_to_cents(150, 2), 150);
}
