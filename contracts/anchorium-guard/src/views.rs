//! Read-only structs returned by the view methods.
use near_sdk::{AccountId, near};

use crate::{Book, Daily};

#[near(serializers = [json])]
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct Roles {
    pub owner: AccountId,
    pub agent: AccountId,
}

#[near(serializers = [json])]
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct Stats {
    pub total_decisions: u32,
    pub open_positions: u32,
    pub paused: bool,
    pub today: Daily,
    /// Paper results from real dry-run quotes. Never mixed with live results.
    pub dry_run: Book,
    /// Results from real NEAR Intents settlement.
    pub live: Book,
}
