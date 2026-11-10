"""Competition configuration independent of the retired broker architecture."""
import os
from pathlib import Path
from dotenv import dotenv_values
from safety_errors import SafetyError

PROJECT_NAME = 'anchorium-perpetual-alpha'
# Public evidence collectors need no wallet settings. An explicit public-only
# process skips dotenv entirely; the same PAPER/network/order checks still apply.
_defaults = ({} if os.environ.get('ANCHORIUM_PUBLIC_ONLY')=='true'
             else dotenv_values(Path(__file__).with_name('.env')))
TRADING_VENUE = os.environ.get(
    'TRADING_VENUE', _defaults.get('TRADING_VENUE') or 'HYPERLIQUID_TESTNET'
).strip().upper()
EXECUTION_MODE = os.environ.get(
    'EXECUTION_MODE', _defaults.get('EXECUTION_MODE') or 'PAPER'
).strip().upper()
ALLOW_LIVE_ORDERS = os.environ.get(
    'ALLOW_LIVE_ORDERS', _defaults.get('ALLOW_LIVE_ORDERS') or 'false'
).strip().lower() == 'true'


class MigrationPolicyError(SafetyError):
    pass


def require_paper_migration(venue='par'):
    """Fail on contradictory settings before the migration runner or simulator.

    Runtime environment changes can revoke paper execution, never enable live
    execution. Manual wallet verification and explicit user authorization remain
    future prerequisites; this module implements no activation path.
    """
    from exchange.venue_profiles import venue_profile
    profile=venue_profile(venue)
    if (os.environ.get('SIGNAL_DATA_MODE',profile.dex.upper()+'_NATIVE_ONLY')!=profile.dex.upper()+'_NATIVE_ONLY' or
            os.environ.get('EXTERNAL_SIGNAL_LIVE_APPROVED','false').lower()!='false'):
        raise MigrationPolicyError('MIGRATION_NATIVE_SIGNAL_SOURCE_REQUIRED')
    if os.environ.get('HL_NETWORK', _defaults.get('HL_NETWORK') or 'testnet').strip().lower()!='testnet':
        raise MigrationPolicyError('MIGRATION_TESTNET_REQUIRED')
    selected=os.environ.get('HL_DEX', (_defaults.get('HL_DEX') or 'par') if profile.dex=='par' else 'pas')
    if selected.strip()!=profile.dex:
        raise MigrationPolicyError('MIGRATION_'+profile.dex.upper()+'_DEX_REQUIRED')
    if (TRADING_VENUE != 'HYPERLIQUID_TESTNET' or
            os.environ.get('TRADING_VENUE', TRADING_VENUE).strip().upper()
            != 'HYPERLIQUID_TESTNET'):
        raise MigrationPolicyError('MIGRATION_TESTNET_REQUIRED')
    if (EXECUTION_MODE != 'PAPER' or
            os.environ.get('EXECUTION_MODE', EXECUTION_MODE).strip().upper() != 'PAPER'):
        raise MigrationPolicyError('MIGRATION_PAPER_MODE_REQUIRED')
    if (ALLOW_LIVE_ORDERS is not False or
            os.environ.get('ALLOW_LIVE_ORDERS',
                           _defaults.get('ALLOW_LIVE_ORDERS') or 'false').strip().lower()
            != 'false'):
        raise MigrationPolicyError('MIGRATION_LIVE_ORDERS_MUST_BE_FALSE')
    return {'TRADING_VENUE': TRADING_VENUE, 'EXECUTION_MODE': 'PAPER',
            'ALLOW_LIVE_ORDERS': False}
