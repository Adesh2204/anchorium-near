"""Explicit practice/scored namespaces. Configuration never grants signing authority."""
from dataclasses import dataclass, asdict
from hashlib import sha256
import json
from .base_exchange import VenueMismatch

BASE_MARKETS = ('BTC','ETH','HYPE','SP500','EWY','EWJ','BRENT','GOLD','USDEUR','USDJPY')


@dataclass(frozen=True)
class VenueProfile:
    dex: str
    collateral: str
    phase: str

    @property
    def prefix(self): return self.dex + ':'

    @property
    def markets(self): return frozenset(self.prefix + x for x in BASE_MARKETS)

    @property
    def identity(self):
        return sha256(json.dumps(asdict(self),sort_keys=True,separators=(',',':')).encode()).hexdigest()


PRACTICE = VenueProfile('par','USDPR','PRACTICE')
SCORED = VenueProfile('pas','USDP','SCORED')


def venue_profile(value='par'):
    if value == 'par' or type(value) is VenueProfile and value == PRACTICE: return PRACTICE
    if value == 'pas' or type(value) is VenueProfile and value == SCORED: return SCORED
    raise VenueMismatch('EXPLICIT_PAR_OR_PAS_PROFILE_REQUIRED')


def symbol_profile(symbol):
    if not isinstance(symbol,str) or symbol.count(':') != 1:
        raise VenueMismatch('EXPLICIT_VERIFIED_MARKET_NAMESPACE_REQUIRED')
    profile = venue_profile(symbol.split(':')[0])
    if symbol not in profile.markets: raise VenueMismatch('AUTHORIZED_MARKET_SCOPE_REQUIRED')
    return profile
