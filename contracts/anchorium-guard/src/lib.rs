//! Anchorium Guard: the on-chain risk policy and decision journal for an autonomous trading agent.
//!
//! Roles:
//! * `owner` (a human) sets the policy, pauses or resumes trading and can replace the agent.
//! * `agent` (the bot's account) journals decisions and their outcomes. Nothing else.
//!
//! The contract re-evaluates every decision the agent journals. A decision is ALLOWED only when the
//! contract's own policy check passes AND the agent itself also allowed it, so a compromised agent can
//! never record an over-limit trade as allowed. Realized PnL is computed by the contract from the
//! reported settlement amounts, not supplied by the agent, and the contract pauses itself when the
//! daily loss cap is reached.
//!
//! Units: USD amounts are integer cents. Settlement amounts are token base units (`U128` strings).
//! Days are UTC days derived from the block timestamp.

use near_sdk::json_types::U128;
use near_sdk::serde_json::{self, json};
use near_sdk::store::{IterableMap, LookupMap, Vector};
use near_sdk::{AccountId, BorshStorageKey, PanicOnDefault, env, near, require};

mod views;
pub use views::*;

pub const EVENT_STANDARD: &str = "anchorium_guard";
pub const EVENT_VERSION: &str = "1.0.0";
pub const MS_PER_DAY: u64 = 86_400_000;
pub const MAX_ASSETS: usize = 16;
pub const MAX_ASSET_LEN: usize = 96;
pub const MAX_TEXT_LEN: usize = 128;
pub const MAX_PAGE: u32 = 50;
pub const MAX_DECISIONS_PER_DAY_CAP: u32 = 500;

#[near(serializers = [json, borsh])]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Side {
    /// Entry: quote asset (USDC) -> asset.
    Buy,
    /// Exit: asset -> quote asset (USDC). Only against an open position tracked by this contract.
    Sell,
}

#[near(serializers = [json, borsh])]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Mode {
    /// Real dry-run quotes, no deposit. Outcomes are paper results.
    DryRun,
    /// Real settlement through NEAR Intents.
    Live,
}

#[near(serializers = [json, borsh])]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum OutcomeStatus {
    Success,
    Refunded,
    Failed,
    DryRun,
}

#[near(serializers = [json, borsh])]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Reason {
    Ok,
    Paused,
    AssetNotAllowed,
    InvalidNotional,
    NotionalAboveMax,
    SlippageAboveMax,
    DailyLossLimit,
    DailyTradeLimit,
    PositionAlreadyOpen,
    MaxOpenPositions,
    Cooldown,
    NoOpenPosition,
    ExitPending,
    /// The contract's checks passed, but the agent itself rejected the trade (for example a failed quote).
    AgentRejected,
}

#[near(serializers = [json, borsh])]
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct Policy {
    /// Asset identifiers the agent may BUY (for example 1Click asset ids such as `nep141:wrap.near`).
    pub allowed_assets: Vec<String>,
    pub max_notional_per_trade_usd_cents: u64,
    /// Maximum allowed entries (BUY decisions) per UTC day.
    pub max_trades_per_day: u32,
    /// Trading pauses once the realized loss of the UTC day reaches this value.
    pub max_daily_loss_usd_cents: u64,
    pub max_slippage_bps: u32,
    /// Minimum seconds between two allowed entries.
    pub cooldown_secs: u64,
    pub max_open_positions: u32,
    /// Hard cap on journaled decisions per UTC day (storage spam protection).
    pub max_decisions_per_day: u32,
    /// Quote asset used to value entries and exits (USDC on NEAR).
    pub quote_asset: String,
    pub quote_asset_decimals: u8,
    /// Kill switch. Blocks new entries; exits of open positions stay possible.
    pub paused: bool,
}

/// Owner update: every field is optional. `paused` is changed only through `pause` and `resume`.
#[near(serializers = [json])]
#[derive(Clone, Default, Debug)]
pub struct PolicyPatch {
    pub allowed_assets: Option<Vec<String>>,
    pub max_notional_per_trade_usd_cents: Option<u64>,
    pub max_trades_per_day: Option<u32>,
    pub max_daily_loss_usd_cents: Option<u64>,
    pub max_slippage_bps: Option<u32>,
    pub cooldown_secs: Option<u64>,
    pub max_open_positions: Option<u32>,
    pub max_decisions_per_day: Option<u32>,
    pub quote_asset: Option<String>,
    pub quote_asset_decimals: Option<u8>,
}

#[near(serializers = [json, borsh])]
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Verdict {
    pub allowed: bool,
    pub reason: Reason,
}

#[near(serializers = [json, borsh])]
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct Outcome {
    pub status: OutcomeStatus,
    pub amount_in: U128,
    pub amount_out: U128,
    pub tx_ref: String,
    /// Realized PnL computed by the contract. Set only for a settled exit (SELL with SUCCESS or DRY_RUN).
    pub realized_pnl_usd_cents: Option<i64>,
    pub timestamp_ms: u64,
}

#[near(serializers = [json, borsh])]
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct Decision {
    pub id: u32,
    pub timestamp_ms: u64,
    pub signal_hash: String,
    pub asset: String,
    pub side: Side,
    pub mode: Mode,
    pub notional_usd_cents: u64,
    pub slippage_bps: u32,
    pub quote_id: String,
    pub agent_allowed: bool,
    pub agent_reason: String,
    pub rationale_hash: Option<String>,
    /// Final verdict: the contract's check AND the agent's own verdict.
    pub allowed: bool,
    pub reason: Reason,
    pub outcome: Option<Outcome>,
}

#[near(serializers = [json, borsh])]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum PositionState {
    /// Entry allowed, settlement not reported yet.
    PendingEntry,
    Open,
}

#[near(serializers = [json, borsh])]
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct Position {
    pub asset: String,
    pub mode: Mode,
    pub state: PositionState,
    pub entry_decision_id: u32,
    /// Quote asset spent on entry, in USD cents (from the reported settlement amount_in).
    pub cost_usd_cents: u64,
    /// Asset amount received on entry, in base units.
    pub amount: U128,
    pub exit_decision_id: Option<u32>,
}

#[near(serializers = [json, borsh])]
#[derive(Clone, Copy, Default, PartialEq, Eq, Debug)]
pub struct Book {
    pub decisions: u32,
    pub allowed: u32,
    pub rejected: u32,
    pub verdict_overrides: u32,
    pub settled: u32,
    pub refunded: u32,
    pub failed: u32,
    pub closed_trades: u32,
    pub wins: u32,
    pub losses: u32,
    pub realized_pnl_usd_cents: i64,
    pub peak_pnl_usd_cents: i64,
    pub max_drawdown_usd_cents: i64,
}

#[near(serializers = [json, borsh])]
#[derive(Clone, Copy, Default, PartialEq, Eq, Debug)]
pub struct Daily {
    /// UTC day number (block_timestamp_ms / 86_400_000).
    pub day: u64,
    pub decisions: u32,
    pub entries: u32,
    /// Realized PnL of the day across paper and live books (paper losses count toward the cap too).
    pub realized_pnl_usd_cents: i64,
}

#[near(serializers = [borsh])]
#[derive(BorshStorageKey)]
enum StorageKey {
    Decisions,
    SignalIndex,
    Positions,
}

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct Contract {
    owner: AccountId,
    agent: AccountId,
    policy: Policy,
    decisions: Vector<Decision>,
    signal_index: LookupMap<String, u32>,
    positions: IterableMap<String, Position>,
    daily: Daily,
    last_entry_ms: Option<u64>,
    dry_run: Book,
    live: Book,
}

pub(crate) fn emit(event: &str, data: serde_json::Value) {
    env::log_str(&format!(
        "EVENT_JSON:{}",
        json!({"standard": EVENT_STANDARD, "version": EVENT_VERSION, "event": event, "data": [data]})
    ));
}

fn is_hex64(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
}

fn require_text(value: &str, max: usize, error: &str) {
    require!(!value.is_empty() && value.len() <= max && !value.chars().any(char::is_control), error);
}

fn validate_policy(p: &Policy) {
    require!(!p.allowed_assets.is_empty() && p.allowed_assets.len() <= MAX_ASSETS, "POLICY_ASSET_COUNT");
    for (i, asset) in p.allowed_assets.iter().enumerate() {
        require_text(asset, MAX_ASSET_LEN, "POLICY_ASSET_INVALID");
        require!(!p.allowed_assets[..i].contains(asset), "POLICY_ASSET_DUPLICATE");
    }
    require!(p.max_notional_per_trade_usd_cents > 0, "POLICY_MAX_NOTIONAL_ZERO");
    require!(p.max_trades_per_day > 0, "POLICY_MAX_TRADES_ZERO");
    require!(p.max_daily_loss_usd_cents > 0, "POLICY_MAX_DAILY_LOSS_ZERO");
    require!(p.max_slippage_bps <= 10_000, "POLICY_SLIPPAGE_ABOVE_100_PERCENT");
    require!(p.max_open_positions > 0, "POLICY_MAX_POSITIONS_ZERO");
    require!(
        p.max_decisions_per_day > 0 && p.max_decisions_per_day <= MAX_DECISIONS_PER_DAY_CAP,
        "POLICY_MAX_DECISIONS_OUT_OF_RANGE"
    );
    require_text(&p.quote_asset, MAX_ASSET_LEN, "POLICY_QUOTE_ASSET_INVALID");
    require!((2..=24).contains(&p.quote_asset_decimals), "POLICY_QUOTE_DECIMALS_OUT_OF_RANGE");
}

/// Converts quote-asset base units to USD cents, rounding down (USDC is treated as 1 USD).
pub fn quote_units_to_cents(amount: u128, decimals: u8) -> u64 {
    let cents = amount / 10u128.pow(u32::from(decimals) - 2);
    u64::try_from(cents).unwrap_or_else(|_| env::panic_str("AMOUNT_OVERFLOW"))
}

impl Book {
    fn close_trade(&mut self, pnl: i64) {
        self.closed_trades += 1;
        if pnl > 0 {
            self.wins += 1;
        } else if pnl < 0 {
            self.losses += 1;
        }
        self.realized_pnl_usd_cents = self.realized_pnl_usd_cents.saturating_add(pnl);
        self.peak_pnl_usd_cents = self.peak_pnl_usd_cents.max(self.realized_pnl_usd_cents);
        self.max_drawdown_usd_cents = self
            .max_drawdown_usd_cents
            .max(self.peak_pnl_usd_cents.saturating_sub(self.realized_pnl_usd_cents));
    }
}

#[near]
impl Contract {
    /// Initializes the guard. `#[private]`: only the contract account itself can call it, which is done in
    /// the same transaction as the deploy.
    #[init]
    #[private]
    pub fn new(owner: AccountId, agent: AccountId, policy: Policy) -> Self {
        require!(owner != agent, "OWNER_AND_AGENT_MUST_DIFFER");
        validate_policy(&policy);
        let this = Self {
            owner,
            agent,
            policy,
            decisions: Vector::new(StorageKey::Decisions),
            signal_index: LookupMap::new(StorageKey::SignalIndex),
            positions: IterableMap::new(StorageKey::Positions),
            daily: Daily { day: env::block_timestamp_ms() / MS_PER_DAY, ..Daily::default() },
            last_entry_ms: None,
            dry_run: Book::default(),
            live: Book::default(),
        };
        emit("initialized", json!({"owner": this.owner, "agent": this.agent, "policy": this.policy}));
        this
    }

    // ----- owner -----

    pub fn update_policy(&mut self, patch: PolicyPatch) -> Policy {
        self.assert_owner();
        let mut next = self.policy.clone();
        if let Some(v) = patch.allowed_assets { next.allowed_assets = v; }
        if let Some(v) = patch.max_notional_per_trade_usd_cents { next.max_notional_per_trade_usd_cents = v; }
        if let Some(v) = patch.max_trades_per_day { next.max_trades_per_day = v; }
        if let Some(v) = patch.max_daily_loss_usd_cents { next.max_daily_loss_usd_cents = v; }
        if let Some(v) = patch.max_slippage_bps { next.max_slippage_bps = v; }
        if let Some(v) = patch.cooldown_secs { next.cooldown_secs = v; }
        if let Some(v) = patch.max_open_positions { next.max_open_positions = v; }
        if let Some(v) = patch.max_decisions_per_day { next.max_decisions_per_day = v; }
        if let Some(v) = patch.quote_asset { next.quote_asset = v; }
        if let Some(v) = patch.quote_asset_decimals { next.quote_asset_decimals = v; }
        validate_policy(&next);
        self.policy = next;
        emit("policy_updated", json!({"policy": self.policy}));
        self.policy.clone()
    }

    pub fn pause(&mut self) {
        self.assert_owner();
        self.policy.paused = true;
        emit("paused", json!({"by": env::predecessor_account_id(), "auto": false}));
    }

    pub fn resume(&mut self) {
        self.assert_owner();
        self.policy.paused = false;
        emit("resumed", json!({"by": env::predecessor_account_id()}));
    }

    pub fn set_agent(&mut self, agent: AccountId) {
        self.assert_owner();
        require!(agent != self.owner, "OWNER_AND_AGENT_MUST_DIFFER");
        emit("agent_changed", json!({"old": self.agent, "new": agent}));
        self.agent = agent;
    }

    // ----- agent -----

    /// Journals one decision. The contract recomputes the verdict; the final verdict is the contract's check
    /// AND the agent's own verdict. Re-sending a known `signal_hash` returns the existing decision unchanged.
    #[allow(clippy::too_many_arguments)]
    pub fn record_decision(
        &mut self,
        signal_hash: String,
        asset: String,
        side: Side,
        mode: Mode,
        notional_usd_cents: u64,
        slippage_bps: u32,
        quote_id: String,
        agent_allowed: bool,
        agent_reason: String,
        rationale_hash: Option<String>,
    ) -> Decision {
        self.assert_agent();
        require!(is_hex64(&signal_hash), "SIGNAL_HASH_MUST_BE_64_LOWERCASE_HEX");
        if let Some(id) = self.signal_index.get(&signal_hash) {
            return self.decisions[*id].clone();
        }
        require_text(&asset, MAX_ASSET_LEN, "ASSET_INVALID");
        require_text(&quote_id, MAX_TEXT_LEN, "QUOTE_ID_INVALID");
        require_text(&agent_reason, MAX_TEXT_LEN, "AGENT_REASON_INVALID");
        if let Some(h) = &rationale_hash {
            require!(is_hex64(h), "RATIONALE_HASH_MUST_BE_64_LOWERCASE_HEX");
        }
        let now = env::block_timestamp_ms();
        self.roll_day(now);
        require!(self.daily.decisions < self.policy.max_decisions_per_day, "DAILY_DECISION_LIMIT");

        let contract_reason = self.evaluate(&asset, side, notional_usd_cents, Some(slippage_bps), now);
        let contract_ok = contract_reason == Reason::Ok;
        let allowed = contract_ok && agent_allowed;
        let reason = if !contract_ok { contract_reason } else if agent_allowed { Reason::Ok } else { Reason::AgentRejected };
        let overridden = agent_allowed && !contract_ok;
        let id = self.decisions.len();

        self.daily.decisions += 1;
        if allowed {
            match side {
                Side::Buy => {
                    self.daily.entries += 1;
                    self.last_entry_ms = Some(now);
                    self.positions.insert(asset.clone(), Position {
                        asset: asset.clone(),
                        mode,
                        state: PositionState::PendingEntry,
                        entry_decision_id: id,
                        cost_usd_cents: 0,
                        amount: U128(0),
                        exit_decision_id: None,
                    });
                }
                Side::Sell => {
                    self.positions.get_mut(&asset).expect("checked by evaluate").exit_decision_id = Some(id);
                }
            }
        }
        let book = self.book_mut(mode);
        book.decisions += 1;
        if allowed { book.allowed += 1 } else { book.rejected += 1 }
        if overridden { book.verdict_overrides += 1 }

        let decision = Decision {
            id,
            timestamp_ms: now,
            signal_hash: signal_hash.clone(),
            asset,
            side,
            mode,
            notional_usd_cents,
            slippage_bps,
            quote_id,
            agent_allowed,
            agent_reason,
            rationale_hash,
            allowed,
            reason,
            outcome: None,
        };
        emit("decision_recorded", json!({
            "id": id, "asset": decision.asset, "side": side, "mode": mode,
            "notional_usd_cents": notional_usd_cents, "slippage_bps": slippage_bps,
            "allowed": allowed, "reason": reason, "signal_hash": signal_hash,
        }));
        if overridden {
            emit("verdict_override", json!({"id": id, "agent_allowed": true, "contract_reason": contract_reason}));
        }
        self.signal_index.insert(signal_hash, id);
        self.decisions.push(decision.clone());
        decision
    }

    /// Journals the settlement of an allowed decision. DRY_RUN decisions take only DRY_RUN outcomes; LIVE
    /// decisions take SUCCESS, REFUNDED or FAILED. Re-sending an identical outcome is a no-op.
    pub fn record_outcome(
        &mut self,
        decision_id: u32,
        status: OutcomeStatus,
        amount_in: U128,
        amount_out: U128,
        tx_ref: String,
    ) -> Decision {
        self.assert_agent();
        require!(decision_id < self.decisions.len(), "UNKNOWN_DECISION");
        let decision = self.decisions[decision_id].clone();
        require!(decision.allowed, "DECISION_NOT_ALLOWED");
        if let Some(existing) = &decision.outcome {
            require!(
                existing.status == status && existing.tx_ref == tx_ref
                    && existing.amount_in == amount_in && existing.amount_out == amount_out,
                "OUTCOME_ALREADY_RECORDED"
            );
            return decision;
        }
        let mode_ok = match decision.mode {
            Mode::DryRun => status == OutcomeStatus::DryRun,
            Mode::Live => status != OutcomeStatus::DryRun,
        };
        require!(mode_ok, "STATUS_MODE_MISMATCH");
        require_text(&tx_ref, MAX_TEXT_LEN, "TX_REF_INVALID");
        let now = env::block_timestamp_ms();
        self.roll_day(now);
        let settled = matches!(status, OutcomeStatus::Success | OutcomeStatus::DryRun);
        let decimals = self.policy.quote_asset_decimals;
        let mut realized = None;

        match (decision.side, settled) {
            (Side::Buy, true) => {
                let position = self.positions.get_mut(&decision.asset).expect("POSITION_MISSING");
                require!(position.entry_decision_id == decision_id, "POSITION_MISMATCH");
                position.state = PositionState::Open;
                position.cost_usd_cents = quote_units_to_cents(amount_in.0, decimals);
                position.amount = amount_out;
            }
            (Side::Buy, false) => {
                self.positions.remove(&decision.asset);
            }
            (Side::Sell, true) => {
                let position = self.positions.remove(&decision.asset).expect("POSITION_MISSING");
                require!(position.exit_decision_id == Some(decision_id), "POSITION_MISMATCH");
                let proceeds = quote_units_to_cents(amount_out.0, decimals);
                let pnl = i64::try_from(i128::from(proceeds) - i128::from(position.cost_usd_cents))
                    .unwrap_or_else(|_| env::panic_str("PNL_OVERFLOW"));
                realized = Some(pnl);
                self.book_mut(decision.mode).close_trade(pnl);
                self.daily.realized_pnl_usd_cents = self.daily.realized_pnl_usd_cents.saturating_add(pnl);
            }
            (Side::Sell, false) => {
                self.positions.get_mut(&decision.asset).expect("POSITION_MISSING").exit_decision_id = None;
            }
        }
        let book = self.book_mut(decision.mode);
        match status {
            OutcomeStatus::Success | OutcomeStatus::DryRun => book.settled += 1,
            OutcomeStatus::Refunded => book.refunded += 1,
            OutcomeStatus::Failed => book.failed += 1,
        }
        let outcome = Outcome { status, amount_in, amount_out, tx_ref, realized_pnl_usd_cents: realized, timestamp_ms: now };
        let mut updated = decision;
        updated.outcome = Some(outcome.clone());
        self.decisions.replace(decision_id, updated.clone());
        emit("outcome_recorded", json!({
            "decision_id": decision_id, "status": status, "amount_in": outcome.amount_in,
            "amount_out": outcome.amount_out, "tx_ref": outcome.tx_ref, "realized_pnl_usd_cents": realized,
        }));
        self.maybe_auto_pause();
        updated
    }

    // ----- views (see views.rs for the read-only structs) -----

    /// Would a trade be allowed right now? Pure view; it changes nothing.
    pub fn check_trade(&self, asset: String, side: Side, notional_usd_cents: u64, slippage_bps: Option<u32>) -> Verdict {
        let reason = self.evaluate(&asset, side, notional_usd_cents, slippage_bps, env::block_timestamp_ms());
        Verdict { allowed: reason == Reason::Ok, reason }
    }

    pub fn get_policy(&self) -> Policy {
        self.policy.clone()
    }

    pub fn get_roles(&self) -> Roles {
        Roles { owner: self.owner.clone(), agent: self.agent.clone() }
    }

    pub fn get_stats(&self) -> Stats {
        Stats {
            total_decisions: self.decisions.len(),
            open_positions: self.positions.len(),
            paused: self.policy.paused,
            today: self.daily_at(env::block_timestamp_ms()),
            dry_run: self.dry_run,
            live: self.live,
        }
    }

    pub fn get_decisions(&self, from_index: u32, limit: u32) -> Vec<Decision> {
        let end = from_index.saturating_add(limit.min(MAX_PAGE)).min(self.decisions.len());
        (from_index..end).map(|i| self.decisions[i].clone()).collect()
    }

    pub fn get_decision(&self, id: u32) -> Option<Decision> {
        self.decisions.get(id).cloned()
    }

    pub fn get_positions(&self) -> Vec<Position> {
        self.positions.values().cloned().collect()
    }
}

impl Contract {
    fn assert_owner(&self) {
        require!(env::predecessor_account_id() == self.owner, "ONLY_OWNER");
    }

    fn assert_agent(&self) {
        require!(env::predecessor_account_id() == self.agent, "ONLY_AGENT");
    }

    fn book_mut(&mut self, mode: Mode) -> &mut Book {
        match mode {
            Mode::DryRun => &mut self.dry_run,
            Mode::Live => &mut self.live,
        }
    }

    fn daily_at(&self, now_ms: u64) -> Daily {
        let day = now_ms / MS_PER_DAY;
        if day == self.daily.day { self.daily } else { Daily { day, ..Daily::default() } }
    }

    fn roll_day(&mut self, now_ms: u64) {
        self.daily = self.daily_at(now_ms);
    }

    fn loss_today(&self, now_ms: u64) -> u64 {
        let pnl = self.daily_at(now_ms).realized_pnl_usd_cents;
        if pnl < 0 { pnl.unsigned_abs() } else { 0 }
    }

    fn maybe_auto_pause(&mut self) {
        let loss = self.loss_today(env::block_timestamp_ms());
        if !self.policy.paused && loss >= self.policy.max_daily_loss_usd_cents {
            self.policy.paused = true;
            emit("auto_paused", json!({
                "reason": "DAILY_LOSS_LIMIT", "loss_today_usd_cents": loss,
                "max_daily_loss_usd_cents": self.policy.max_daily_loss_usd_cents,
            }));
        }
    }

    fn evaluate(&self, asset: &str, side: Side, notional: u64, slippage: Option<u32>, now_ms: u64) -> Reason {
        let p = &self.policy;
        if notional == 0 {
            return Reason::InvalidNotional;
        }
        if slippage.is_some_and(|s| s > p.max_slippage_bps) {
            return Reason::SlippageAboveMax;
        }
        match side {
            // Exits reduce risk: they ignore pause, daily limits and cooldown, but need an open position.
            Side::Sell => match self.positions.get(asset) {
                Some(pos) if pos.state == PositionState::Open => {
                    if pos.exit_decision_id.is_some() { Reason::ExitPending } else { Reason::Ok }
                }
                _ => Reason::NoOpenPosition,
            },
            Side::Buy => {
                let daily = self.daily_at(now_ms);
                if p.paused {
                    Reason::Paused
                } else if !p.allowed_assets.iter().any(|a| a == asset) {
                    Reason::AssetNotAllowed
                } else if notional > p.max_notional_per_trade_usd_cents {
                    Reason::NotionalAboveMax
                } else if self.loss_today(now_ms) >= p.max_daily_loss_usd_cents {
                    Reason::DailyLossLimit
                } else if daily.entries >= p.max_trades_per_day {
                    Reason::DailyTradeLimit
                } else if self.positions.contains_key(asset) {
                    Reason::PositionAlreadyOpen
                } else if self.positions.len() >= p.max_open_positions {
                    Reason::MaxOpenPositions
                } else if self
                    .last_entry_ms
                    .is_some_and(|t| now_ms.saturating_sub(t) < p.cooldown_secs.saturating_mul(1000))
                {
                    Reason::Cooldown
                } else {
                    Reason::Ok
                }
            }
        }
    }
}

#[cfg(test)]
mod tests;
