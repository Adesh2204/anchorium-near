use super::*;
use near_sdk::test_utils::{VMContextBuilder, get_logs};
use near_sdk::testing_env;

fn acc(s: &str) -> AccountId {
    s.parse().unwrap()
}

const USDC: &str = "usdc.test";
const WNEAR: &str = "wrap.test";
const DEPOSIT: &str = "1c0ffee000000000000000000000000000000000000000000000000000000001";
const NOW: u64 = 1_794_400_000_000; // a fixed ms timestamp

fn ctx(predecessor: &str) {
    ctx_with(predecessor, 0);
}

fn ctx_with(predecessor: &str, yocto: u128) {
    testing_env!(VMContextBuilder::new()
        .current_account_id(acc("vault.test"))
        .predecessor_account_id(acc(predecessor))
        .block_timestamp(NOW * 1_000_000)
        .prepaid_gas(Gas::from_tgas(300)) // the protocol maximum per call; several releases share it in one test
        .attached_deposit(NearToken::from_yoctonear(yocto))
        .build());
}

fn vault(accept_dry_run: bool) -> Vault {
    ctx("owner.test");
    Vault::new(acc("owner.test"), acc("agent.test"), acc("guard.test"), acc(USDC), 6,
               vec![acc(USDC), acc(WNEAR)], accept_dry_run, 5, U128(10_000_000),
               U128(5 * STORAGE_REGISTRATION.as_yoctonear()))
}

fn buy(id: u32) -> GuardDecision {
    GuardDecision { id, asset: format!("nep141:{WNEAR}"), side: "BUY".into(), mode: "LIVE".into(),
                    notional_usd_cents: 200, quote_id: format!("corr-{id}:{DEPOSIT}"), allowed: true, outcome: None }
}

fn sell(id: u32) -> GuardDecision {
    GuardDecision { side: "SELL".into(), ..buy(id) }
}

fn open(amount: u128) -> Vec<GuardPosition> {
    vec![GuardPosition { asset: format!("nep141:{WNEAR}"), mode: "LIVE".into(), state: "OPEN".into(), amount: U128(amount) }]
}

fn check(v: &Vault, d: &GuardDecision, pos: &[GuardPosition], token: &str, amount: u128) -> Result<(), &'static str> {
    v.check_release(Some(d), pos, d.id, &acc(token), amount, &acc(DEPOSIT), &d.quote_id, false, NOW)
}

#[test]
fn buy_happy_path_and_every_buy_rule() {
    let v = vault(false);
    assert_eq!(check(&v, &buy(1), &[], USDC, 2_000_000), Ok(()));
    assert_eq!(check(&v, &buy(1), &[], USDC, 2_000_001), Err("AMOUNT_ABOVE_NOTIONAL"));
    assert_eq!(check(&v, &buy(1), &[], WNEAR, 1), Err("WRONG_TOKEN"));
    assert_eq!(check(&v, &buy(1), &[], "evil.test", 1), Err("TOKEN_NOT_ALLOWED"));
    assert_eq!(check(&v, &GuardDecision { allowed: false, ..buy(1) }, &[], USDC, 1), Err("NOT_ALLOWED"));
    assert_eq!(check(&v, &GuardDecision { mode: "DRY_RUN".into(), ..buy(1) }, &[], USDC, 1), Err("DRY_RUN_DECISION"));
    assert_eq!(check(&v, &GuardDecision { outcome: Some(json!({"status": "SUCCESS"})), ..buy(1) }, &[], USDC, 1),
               Err("ALREADY_SETTLED"));
    assert_eq!(v.check_release(None, &[], 1, &acc(USDC), 1, &acc(DEPOSIT), "q", false, NOW), Err("NO_SUCH_DECISION"));
    assert_eq!(v.check_release(Some(&buy(2)), &[], 1, &acc(USDC), 1, &acc(DEPOSIT), &buy(2).quote_id, false, NOW),
               Err("DECISION_ID_MISMATCH"));
    assert_eq!(v.check_release(Some(&buy(1)), &[], 1, &acc(USDC), 1, &acc(DEPOSIT), "other-quote", false, NOW),
               Err("QUOTE_MISMATCH"));
    assert_eq!(v.check_release(Some(&buy(1)), &[], 1, &acc(USDC), 1, &acc("thief.test"), &buy(1).quote_id, false, NOW),
               Err("DEPOSIT_ADDRESS_MISMATCH"));
}

#[test]
fn dry_run_only_on_a_demo_deployment() {
    let demo = vault(true);
    assert_eq!(check(&demo, &GuardDecision { mode: "DRY_RUN".into(), ..buy(1) }, &[], USDC, 1), Ok(()));
    assert_eq!(check(&demo, &GuardDecision { mode: "PAPER".into(), ..buy(1) }, &[], USDC, 1), Err("UNKNOWN_MODE"));
}

#[test]
fn sell_needs_an_open_position_of_the_same_asset_and_mode() {
    let v = vault(false);
    assert_eq!(check(&v, &sell(3), &open(5), WNEAR, 5), Ok(()));
    assert_eq!(check(&v, &sell(3), &open(5), WNEAR, 6), Err("AMOUNT_ABOVE_POSITION"));
    assert_eq!(check(&v, &sell(3), &open(5), USDC, 5), Err("WRONG_TOKEN"));
    assert_eq!(check(&v, &sell(3), &[], WNEAR, 1), Err("NO_OPEN_POSITION"));
    let dry_pos = vec![GuardPosition { mode: "DRY_RUN".into(), ..open(5)[0].clone() }];
    assert_eq!(check(&v, &sell(3), &dry_pos, WNEAR, 1), Err("NO_OPEN_POSITION"));
    let pending = vec![GuardPosition { state: "PENDING_ENTRY".into(), ..open(5)[0].clone() }];
    assert_eq!(check(&v, &sell(3), &pending, WNEAR, 1), Err("NO_OPEN_POSITION"));
}

#[test]
fn release_marks_executed_and_counts_the_day() {
    let mut v = vault(false);
    ctx("vault.test");
    let r = v.on_guard_answer(Ok(Some(buy(1))), Ok(vec![]), 1, acc(USDC), U128(2_000_000), acc(DEPOSIT),
                              buy(1).quote_id, true);
    assert!(matches!(r, PromiseOrValue::Promise(_)));
    assert!(v.is_executed(1));
    let day = v.get_daily();
    assert_eq!((day.releases, day.quote_released.0, day.storage_spent.0), (1, 2_000_000, STORAGE_REGISTRATION.as_yoctonear()));
    assert!(get_logs().iter().any(|l| l.contains("\"event\":\"vault_release\"")));
    // the same decision again, even if the agent races a second execute: refused in the callback
    let again = v.on_guard_answer(Ok(Some(buy(1))), Ok(vec![]), 1, acc(USDC), U128(1), acc(DEPOSIT), buy(1).quote_id, false);
    assert!(matches!(again, PromiseOrValue::Value(false)));
    assert!(get_logs().iter().any(|l| l.contains("ALREADY_EXECUTED")));
}

#[test]
fn rejected_release_emits_an_event_and_moves_nothing() {
    let mut v = vault(false);
    ctx("vault.test");
    let r = v.on_guard_answer(Ok(Some(GuardDecision { allowed: false, ..buy(4) })), Ok(vec![]), 4, acc(USDC),
                              U128(1), acc(DEPOSIT), buy(4).quote_id, false);
    assert!(matches!(r, PromiseOrValue::Value(false)));
    assert!(!v.is_executed(4));
    assert!(get_logs().iter().any(|l| l.contains("vault_release_rejected") && l.contains("NOT_ALLOWED")));
    let unreachable = v.on_guard_answer(Err(PromiseError::Failed), Ok(vec![]), 4, acc(USDC), U128(1), acc(DEPOSIT),
                                        "q".into(), false);
    assert!(matches!(unreachable, PromiseOrValue::Value(false)));
}

#[test]
fn failed_transfer_unmarks_and_rolls_back() {
    let mut v = vault(false);
    ctx("vault.test");
    let _ = v.on_guard_answer(Ok(Some(buy(5))), Ok(vec![]), 5, acc(USDC), U128(1_500_000), acc(DEPOSIT), buy(5).quote_id, false);
    assert!(v.is_executed(5));
    assert!(!v.on_transferred(Err(PromiseError::Failed), 5, acc(USDC), U128(1_500_000), U128(1_500_000)));
    assert!(!v.is_executed(5));
    assert_eq!((v.get_daily().releases, v.get_daily().quote_released.0), (0, 0));
    assert!(get_logs().iter().any(|l| l.contains("release_failed")));
    assert!(v.on_transferred(Ok(()), 5, acc(USDC), U128(1), U128(1)));
}

#[test]
fn daily_caps() {
    let mut v = vault(false);
    ctx("vault.test");
    for id in 10..15 {
        let _ = v.on_guard_answer(Ok(Some(buy(id))), Ok(vec![]), id, acc(USDC), U128(1_000_000), acc(DEPOSIT),
                                  buy(id).quote_id, false);
    }
    assert_eq!(check(&v, &buy(20), &[], USDC, 1), Err("DAILY_RELEASE_CAP"));
    let mut v2 = vault(false);
    ctx("vault.test");
    v2.max_quote_per_day = 5_000_000; // $5 a day
    for id in 30..32 {
        let _ = v2.on_guard_answer(Ok(Some(buy(id))), Ok(vec![]), id, acc(USDC), U128(2_000_000), acc(DEPOSIT),
                                   buy(id).quote_id, false);
    }
    assert_eq!(check(&v2, &buy(40), &[], USDC, 2_000_001), Err("AMOUNT_ABOVE_NOTIONAL"));
    assert_eq!(check(&v2, &buy(40), &[], USDC, 2_000_000), Err("DAILY_QUOTE_CAP"));
    let mut v3 = vault(false);
    ctx("vault.test");
    v3.max_storage_per_day = STORAGE_REGISTRATION.as_yoctonear();
    let _ = v3.on_guard_answer(Ok(Some(buy(50))), Ok(vec![]), 50, acc(USDC), U128(1), acc(DEPOSIT), buy(50).quote_id, true);
    assert_eq!(v3.check_release(Some(&buy(51)), &[], 51, &acc(USDC), 1, &acc(DEPOSIT), &buy(51).quote_id, true, NOW),
               Err("DAILY_STORAGE_CAP"));
    // a new day resets the counters
    assert_eq!(v.check_release(Some(&buy(20)), &[], 20, &acc(USDC), 1, &acc(DEPOSIT), &buy(20).quote_id, false,
                               NOW + DAY_MS), Ok(()));
}

#[test]
#[should_panic(expected = "ONLY_AGENT")]
fn only_the_agent_can_execute() {
    let mut v = vault(false);
    ctx("stranger.test");
    let _ = v.execute(1, acc(USDC), U128(1), acc(DEPOSIT), "q".into(), None);
}

#[test]
#[should_panic(expected = "VAULT_PAUSED")]
fn paused_vault_refuses_execute() {
    let mut v = vault(false);
    ctx("owner.test");
    v.pause();
    ctx("agent.test");
    let _ = v.execute(1, acc(USDC), U128(1), acc(DEPOSIT), "q".into(), None);
}

#[test]
#[should_panic(expected = "ALREADY_EXECUTED")]
fn executed_decision_cannot_be_replayed() {
    let mut v = vault(false);
    ctx("vault.test");
    let _ = v.on_guard_answer(Ok(Some(buy(1))), Ok(vec![]), 1, acc(USDC), U128(1), acc(DEPOSIT), buy(1).quote_id, false);
    ctx("agent.test");
    let _ = v.execute(1, acc(USDC), U128(1), acc(DEPOSIT), buy(1).quote_id, None);
}

#[test]
fn owner_controls_and_paused_withdraw() {
    let mut v = vault(false);
    ctx("owner.test");
    v.pause();                                          // pausing needs no deposit
    ctx_with("owner.test", 1);
    let _ = v.withdraw(acc(USDC), U128(5));            // works while paused
    v.resume();
    v.set_agent(acc("agent2.test"));
    assert_eq!(v.get_config().agent, acc("agent2.test"));
    assert!(!v.get_config().paused);
}

#[test]
#[should_panic(expected = "ONLY_OWNER")]
fn agent_cannot_withdraw() {
    let mut v = vault(false);
    ctx_with("agent.test", 1);
    let _ = v.withdraw(acc(USDC), U128(5));
}

#[test]
#[should_panic(expected = "Requires attached deposit of exactly 1 yoctoNEAR")]
fn withdraw_needs_a_full_access_key() {
    let mut v = vault(false);
    ctx("owner.test");
    let _ = v.withdraw(acc(USDC), U128(5));
}

#[test]
#[should_panic(expected = "NOT_ENOUGH_GAS")]
fn execute_needs_enough_gas_for_its_callbacks() {
    let mut v = vault(false);
    testing_env!(VMContextBuilder::new().current_account_id(acc("vault.test")).predecessor_account_id(acc("agent.test"))
        .prepaid_gas(Gas::from_tgas(100)).build());
    let _ = v.execute(1, acc(USDC), U128(1), acc(DEPOSIT), "q".into(), None);
}

#[test]
fn deposits_only_from_the_owner_in_allowed_tokens() {
    let mut v = vault(false);
    ctx(USDC);
    assert!(matches!(v.ft_on_transfer(acc("owner.test"), U128(7), String::new()), PromiseOrValue::Value(U128(0))));
    assert!(matches!(v.ft_on_transfer(acc("stranger.test"), U128(7), String::new()), PromiseOrValue::Value(U128(7))));
    ctx("evil.test");
    assert!(matches!(v.ft_on_transfer(acc("owner.test"), U128(7), String::new()), PromiseOrValue::Value(U128(7))));
}

#[test]
#[should_panic(expected = "QUOTE_TOKEN_MUST_BE_ALLOWED")]
fn init_requires_the_quote_token_to_be_allowed() {
    ctx("owner.test");
    let _ = Vault::new(acc("owner.test"), acc("agent.test"), acc("guard.test"), acc(USDC), 6, vec![acc(WNEAR)], false, 1,
                       U128(1), U128(1));
}
