//! Anchorium Vault: the contract, not the bot, releases the money.
//!
//! The vault holds the agent's trading funds (NEP-141 tokens, e.g. USDC and wNEAR). The agent can move them only by
//! calling `execute(decision_id, ..)` with its function-call key, and the vault releases tokens only after reading
//! that decision from the Anchorium guard contract and checking ALL of:
//!   * the decision exists and the guard ALLOWED it;
//!   * it is a LIVE decision (DRY_RUN only on a deployment initialised with `accept_dry_run = true`, the demo);
//!   * it has no outcome yet, and the vault has not executed it before;
//!   * the `quote_id` matches the journaled one, and when the journaled quote id carries a deposit address
//!     (`<correlationId>:<depositAddress>`, how the agent journals LIVE quotes) the payout goes to exactly that address;
//!   * BUY: the token is the quote token and the amount is at most the decision's notional;
//!     SELL: the token is the decision's asset and the amount is at most the open position held by the guard;
//!   * the vault is not paused and the daily release caps are not exceeded.
//!
//! The decision is marked executed BEFORE the transfer; if the transfer fails, a callback un-marks it.
//!
//! Residual risk (documented in docs/VAULT.md): the contract cannot check off-chain that a deposit address belongs to
//! the 1Click quote. A compromised agent can therefore misdirect at most one allowed decision's notional, bounded by
//! the guard's per-trade cap and daily limits and by this vault's daily caps.
use near_sdk::json_types::U128;
use near_sdk::serde_json::{Value, json};
use near_sdk::store::LookupSet;
use near_sdk::{
    AccountId, BorshStorageKey, Gas, NearToken, PanicOnDefault, Promise, PromiseError, PromiseOrValue, assert_one_yocto,
    env, ext_contract, near, require,
};

#[cfg(test)]
mod tests;

pub const EVENT_STANDARD: &str = "anchorium_vault";
pub const EVENT_VERSION: &str = "1.0.0";
pub const DAY_MS: u64 = 86_400_000;
/// `execute` refuses less prepaid gas than this, so the callbacks below can never run out of gas mid-flow.
pub const MIN_EXECUTE_GAS: Gas = Gas::from_tgas(150);
const GAS_VIEW: Gas = Gas::from_tgas(10);
const GAS_ON_GUARD: Gas = Gas::from_tgas(90);
const GAS_STORAGE: Gas = Gas::from_tgas(10);
const GAS_FT_TRANSFER: Gas = Gas::from_tgas(15);
const GAS_ON_TRANSFER: Gas = Gas::from_tgas(15);
const ONE_YOCTO: NearToken = NearToken::from_yoctonear(1);
/// NEP-145 registration of a new 1Click deposit address on a NEP-141 token (USDC and wNEAR both ask 0.00125 NEAR).
pub const STORAGE_REGISTRATION: NearToken = NearToken::from_yoctonear(1_250_000_000_000_000_000_000);

#[derive(BorshStorageKey)]
#[near]
enum Key {
    Executed,
}

/// The subset of the guard's `Decision` the vault reads (extra fields in the guard's JSON are ignored).
#[near(serializers = [json])]
#[derive(Clone, Debug, PartialEq)]
pub struct GuardDecision {
    pub id: u32,
    pub asset: String,
    pub side: String,
    pub mode: String,
    pub notional_usd_cents: u64,
    pub quote_id: String,
    pub allowed: bool,
    #[serde(default)]
    pub outcome: Option<Value>,
}

/// The subset of the guard's `Position` the vault reads.
#[near(serializers = [json])]
#[derive(Clone, Debug, PartialEq)]
pub struct GuardPosition {
    pub asset: String,
    pub mode: String,
    pub state: String,
    pub amount: U128,
}

#[ext_contract(ext_guard)]
#[allow(dead_code)]
trait Guard {
    fn get_decision(&self, id: u32) -> Option<GuardDecision>;
    fn get_positions(&self) -> Vec<GuardPosition>;
}

#[ext_contract(ext_ft)]
#[allow(dead_code)]
trait FungibleToken {
    fn ft_transfer(&mut self, receiver_id: AccountId, amount: U128, memo: Option<String>);
    fn storage_deposit(&mut self, account_id: Option<AccountId>, registration_only: Option<bool>);
}

#[near(serializers = [json])]
#[derive(Clone, Debug, PartialEq)]
pub struct Config {
    pub owner: AccountId,
    pub agent: AccountId,
    pub guard: AccountId,
    pub quote_token: AccountId,
    pub quote_decimals: u8,
    pub allowed_tokens: Vec<AccountId>,
    pub paused: bool,
    pub accept_dry_run: bool,
    pub max_releases_per_day: u32,
    pub max_quote_per_day: U128,
    pub max_storage_per_day: U128,
}

#[near(serializers = [json])]
#[derive(Clone, Debug, PartialEq)]
pub struct Daily {
    pub day: u64,
    pub releases: u32,
    pub quote_released: U128,
    pub storage_spent: U128,
    pub releases_total: u32,
}

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct Vault {
    owner: AccountId,
    agent: AccountId,
    guard: AccountId,
    quote_token: AccountId,
    quote_decimals: u8,
    allowed_tokens: Vec<AccountId>,
    executed: LookupSet<u32>,
    paused: bool,
    accept_dry_run: bool,
    max_releases_per_day: u32,
    max_quote_per_day: u128,
    max_storage_per_day: u128,
    day: u64,
    releases_today: u32,
    quote_released_today: u128,
    storage_spent_today: u128,
    releases_total: u32,
}

fn emit(event: &str, data: Value) {
    env::log_str(&format!(
        "EVENT_JSON:{}",
        json!({"standard": EVENT_STANDARD, "version": EVENT_VERSION, "event": event, "data": [data]})
    ));
}

/// "nep141:wrap.near" -> "wrap.near"
fn token_of(asset: &str) -> &str {
    asset.strip_prefix("nep141:").unwrap_or(asset)
}

#[near]
impl Vault {
    #[init]
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        owner: AccountId,
        agent: AccountId,
        guard: AccountId,
        quote_token: AccountId,
        quote_decimals: u8,
        allowed_tokens: Vec<AccountId>,
        accept_dry_run: bool,
        max_releases_per_day: u32,
        max_quote_per_day: U128,
        max_storage_per_day: U128,
    ) -> Self {
        require!(allowed_tokens.contains(&quote_token), "QUOTE_TOKEN_MUST_BE_ALLOWED");
        require!((2..=24).contains(&quote_decimals), "QUOTE_DECIMALS_OUT_OF_RANGE");
        require!(owner != agent, "OWNER_AND_AGENT_MUST_DIFFER");
        Self {
            owner,
            agent,
            guard,
            quote_token,
            quote_decimals,
            allowed_tokens,
            executed: LookupSet::new(Key::Executed),
            paused: false,
            accept_dry_run,
            max_releases_per_day,
            max_quote_per_day: max_quote_per_day.0,
            max_storage_per_day: max_storage_per_day.0,
            day: 0,
            releases_today: 0,
            quote_released_today: 0,
            storage_spent_today: 0,
            releases_total: 0,
        }
    }

    // ----- agent -----

    /// Agent only (its function-call key; no deposit needed). Reads the decision and the open positions from the guard,
    /// then releases `amount` of `token` to `deposit_address` if every rule holds (see the crate docs).
    /// `register = true` first registers the deposit address on the token (0.00125 NEAR from the vault, daily-capped).
    pub fn execute(
        &mut self,
        decision_id: u32,
        token: AccountId,
        amount: U128,
        deposit_address: AccountId,
        quote_id: String,
        register: Option<bool>,
    ) -> Promise {
        require!(env::predecessor_account_id() == self.agent, "ONLY_AGENT");
        require!(env::prepaid_gas() >= MIN_EXECUTE_GAS, "NOT_ENOUGH_GAS_ATTACH_150_TGAS");
        require!(!self.paused, "VAULT_PAUSED");
        require!(!self.executed.contains(&decision_id), "ALREADY_EXECUTED");
        require!(amount.0 > 0, "AMOUNT_MUST_BE_POSITIVE");
        require!(self.allowed_tokens.contains(&token), "TOKEN_NOT_ALLOWED");
        ext_guard::ext(self.guard.clone())
            .with_static_gas(GAS_VIEW)
            .get_decision(decision_id)
            .and(ext_guard::ext(self.guard.clone()).with_static_gas(GAS_VIEW).get_positions())
            .then(Self::ext(env::current_account_id()).with_static_gas(GAS_ON_GUARD).on_guard_answer(
                decision_id,
                token,
                amount,
                deposit_address,
                quote_id,
                register.unwrap_or(false),
            ))
    }

    #[private]
    #[allow(clippy::too_many_arguments)]
    pub fn on_guard_answer(
        &mut self,
        #[callback_result] decision: Result<Option<GuardDecision>, PromiseError>,
        #[callback_result] positions: Result<Vec<GuardPosition>, PromiseError>,
        decision_id: u32,
        token: AccountId,
        amount: U128,
        deposit_address: AccountId,
        quote_id: String,
        register: bool,
    ) -> PromiseOrValue<bool> {
        let now = env::block_timestamp_ms();
        let checked = match (decision, positions) {
            (Ok(d), Ok(p)) => {
                self.check_release(d.as_ref(), &p, decision_id, &token, amount.0, &deposit_address, &quote_id, register, now)
            }
            _ => Err("GUARD_UNREACHABLE"),
        };
        if let Err(reason) = checked {
            emit("vault_release_rejected", json!({"decision_id": decision_id, "reason": reason, "token": token,
                                                  "amount": amount, "deposit_address": deposit_address}));
            return PromiseOrValue::Value(false);
        }
        // Mark first: a second execute for the same decision is refused even while this transfer is in flight.
        self.roll_day(now);
        self.executed.insert(decision_id);
        self.releases_today += 1;
        self.releases_total += 1;
        let quote_counted = if token == self.quote_token { amount.0 } else { 0 };
        self.quote_released_today += quote_counted;
        let storage_counted = if register { STORAGE_REGISTRATION.as_yoctonear() } else { 0 };
        self.storage_spent_today += storage_counted;
        emit("vault_release", json!({"decision_id": decision_id, "token": token, "amount": amount,
                                     "deposit_address": deposit_address, "quote_id": quote_id, "register": register}));
        let transfer = ext_ft::ext(token.clone())
            .with_attached_deposit(ONE_YOCTO)
            .with_static_gas(GAS_FT_TRANSFER)
            .ft_transfer(deposit_address.clone(), amount, Some(format!("anchorium decision {decision_id}")));
        let chain = if register {
            ext_ft::ext(token.clone())
                .with_attached_deposit(STORAGE_REGISTRATION)
                .with_static_gas(GAS_STORAGE)
                .storage_deposit(Some(deposit_address), Some(true))
                .then(transfer)
        } else {
            transfer
        };
        PromiseOrValue::Promise(chain.then(
            Self::ext(env::current_account_id()).with_static_gas(GAS_ON_TRANSFER).on_transferred(
                decision_id,
                token,
                amount,
                U128(quote_counted),
            ),
        ))
    }

    /// If the transfer failed, the decision is released for a later retry and the day's counters are rolled back
    /// (a storage registration that succeeded stays paid).
    #[private]
    pub fn on_transferred(
        &mut self,
        #[callback_result] transfer: Result<(), PromiseError>,
        decision_id: u32,
        token: AccountId,
        amount: U128,
        quote_counted: U128,
    ) -> bool {
        if transfer.is_ok() {
            return true;
        }
        self.executed.remove(&decision_id);
        self.releases_today = self.releases_today.saturating_sub(1);
        self.releases_total = self.releases_total.saturating_sub(1);
        self.quote_released_today = self.quote_released_today.saturating_sub(quote_counted.0);
        emit("release_failed", json!({"decision_id": decision_id, "token": token, "amount": amount}));
        false
    }

    // ----- owner -----

    /// Deposits: only `allowed_tokens`, only from the owner (`ft_transfer_call`); anything else is returned in full.
    pub fn ft_on_transfer(&mut self, sender_id: AccountId, amount: U128, msg: String) -> PromiseOrValue<U128> {
        let token = env::predecessor_account_id();
        let _ = msg;
        if self.allowed_tokens.contains(&token) && sender_id == self.owner {
            emit("deposit", json!({"token": token, "amount": amount, "from": sender_id}));
            PromiseOrValue::Value(U128(0))
        } else {
            PromiseOrValue::Value(amount)
        }
    }

    /// Owner only, with a full-access key (exactly 1 yoctoNEAR attached); works while paused.
    #[payable]
    pub fn withdraw(&mut self, token: AccountId, amount: U128) -> Promise {
        assert_one_yocto();
        self.assert_owner();
        require!(amount.0 > 0, "AMOUNT_MUST_BE_POSITIVE");
        emit("withdraw", json!({"token": token, "amount": amount, "to": self.owner}));
        ext_ft::ext(token)
            .with_attached_deposit(ONE_YOCTO)
            .with_static_gas(GAS_FT_TRANSFER)
            .ft_transfer(self.owner.clone(), amount, Some("anchorium vault withdraw".to_string()))
    }

    pub fn pause(&mut self) {
        self.assert_owner();
        self.paused = true;
        emit("paused", json!({"by": self.owner}));
    }

    /// Re-enabling releases needs the owner's full-access key (1 yoctoNEAR); pausing does not.
    #[payable]
    pub fn resume(&mut self) {
        assert_one_yocto();
        self.assert_owner();
        self.paused = false;
        emit("resumed", json!({"by": self.owner}));
    }

    #[payable]
    pub fn set_agent(&mut self, agent: AccountId) {
        assert_one_yocto();
        self.assert_owner();
        require!(agent != self.owner, "OWNER_AND_AGENT_MUST_DIFFER");
        emit("agent_changed", json!({"from": self.agent, "to": agent}));
        self.agent = agent;
    }

    // ----- views -----

    pub fn get_config(&self) -> Config {
        Config {
            owner: self.owner.clone(),
            agent: self.agent.clone(),
            guard: self.guard.clone(),
            quote_token: self.quote_token.clone(),
            quote_decimals: self.quote_decimals,
            allowed_tokens: self.allowed_tokens.clone(),
            paused: self.paused,
            accept_dry_run: self.accept_dry_run,
            max_releases_per_day: self.max_releases_per_day,
            max_quote_per_day: U128(self.max_quote_per_day),
            max_storage_per_day: U128(self.max_storage_per_day),
        }
    }

    pub fn is_executed(&self, decision_id: u32) -> bool {
        self.executed.contains(&decision_id)
    }

    pub fn get_daily(&self) -> Daily {
        let today = env::block_timestamp_ms() / DAY_MS;
        let same = today == self.day;
        Daily {
            day: today,
            releases: if same { self.releases_today } else { 0 },
            quote_released: U128(if same { self.quote_released_today } else { 0 }),
            storage_spent: U128(if same { self.storage_spent_today } else { 0 }),
            releases_total: self.releases_total,
        }
    }
}

impl Vault {
    fn assert_owner(&self) {
        require!(env::predecessor_account_id() == self.owner, "ONLY_OWNER");
    }

    fn roll_day(&mut self, now_ms: u64) {
        let today = now_ms / DAY_MS;
        if today != self.day {
            self.day = today;
            self.releases_today = 0;
            self.quote_released_today = 0;
            self.storage_spent_today = 0;
        }
    }

    /// Every release rule, as a pure function of the guard's answer and the vault's state (unit-tested one by one).
    #[allow(clippy::too_many_arguments)]
    pub fn check_release(
        &self,
        decision: Option<&GuardDecision>,
        positions: &[GuardPosition],
        decision_id: u32,
        token: &AccountId,
        amount: u128,
        deposit_address: &AccountId,
        quote_id: &str,
        register: bool,
        now_ms: u64,
    ) -> Result<(), &'static str> {
        let d = decision.ok_or("NO_SUCH_DECISION")?;
        if d.id != decision_id {
            return Err("DECISION_ID_MISMATCH");
        }
        if !d.allowed {
            return Err("NOT_ALLOWED");
        }
        match d.mode.as_str() {
            "LIVE" => {}
            "DRY_RUN" if self.accept_dry_run => {}
            "DRY_RUN" => return Err("DRY_RUN_DECISION"),
            _ => return Err("UNKNOWN_MODE"),
        }
        if d.outcome.is_some() {
            return Err("ALREADY_SETTLED");
        }
        if self.executed.contains(&decision_id) {
            return Err("ALREADY_EXECUTED");
        }
        if self.paused {
            return Err("VAULT_PAUSED");
        }
        if d.quote_id != quote_id {
            return Err("QUOTE_MISMATCH");
        }
        if let Some((_, journaled_address)) = d.quote_id.rsplit_once(':')
            && journaled_address != deposit_address.as_str()
        {
            return Err("DEPOSIT_ADDRESS_MISMATCH");
        }
        if !self.allowed_tokens.contains(token) {
            return Err("TOKEN_NOT_ALLOWED");
        }
        match d.side.as_str() {
            "BUY" => {
                if token != &self.quote_token {
                    return Err("WRONG_TOKEN");
                }
                let max = (d.notional_usd_cents as u128)
                    .checked_mul(10u128.pow((self.quote_decimals - 2) as u32))
                    .ok_or("NOTIONAL_OVERFLOW")?;
                if amount > max {
                    return Err("AMOUNT_ABOVE_NOTIONAL");
                }
            }
            "SELL" => {
                if token.as_str() != token_of(&d.asset) {
                    return Err("WRONG_TOKEN");
                }
                let held = positions
                    .iter()
                    .find(|p| p.asset == d.asset && p.mode == d.mode && p.state == "OPEN")
                    .ok_or("NO_OPEN_POSITION")?;
                if amount > held.amount.0 {
                    return Err("AMOUNT_ABOVE_POSITION");
                }
            }
            _ => return Err("UNKNOWN_SIDE"),
        }
        let same_day = now_ms / DAY_MS == self.day;
        let (releases, quote, storage) = if same_day {
            (self.releases_today, self.quote_released_today, self.storage_spent_today)
        } else {
            (0, 0, 0)
        };
        if releases >= self.max_releases_per_day {
            return Err("DAILY_RELEASE_CAP");
        }
        if token == &self.quote_token && quote + amount > self.max_quote_per_day {
            return Err("DAILY_QUOTE_CAP");
        }
        if register && storage + STORAGE_REGISTRATION.as_yoctonear() > self.max_storage_per_day {
            return Err("DAILY_STORAGE_CAP");
        }
        Ok(())
    }
}
