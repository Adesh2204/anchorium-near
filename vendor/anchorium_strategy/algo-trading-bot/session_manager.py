"""Continuous venue clock; the frozen brain retains its UTC daily date groups."""
from datetime import datetime, timezone

from safety_errors import SafetyError


class SessionManager:
    anchor_policy = 'UTC_DAILY'

    def __init__(self, anchor_policy='UTC_DAILY'):
        if anchor_policy != self.anchor_policy:
            raise SafetyError('UNVALIDATED_VWAP_ANCHOR')

    def localize(self, value=None):
        value = value or datetime.now(timezone.utc)
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise SafetyError('AWARE_UTC_TIMESTAMP_REQUIRED')
        return value.astimezone(timezone.utc)

    def get_vwap_anchor(self, value=None):
        return self.localize(value).replace(hour=0, minute=0, second=0, microsecond=0)

    def is_eod_square_off(self, value):
        self.localize(value)
        return False

    def can_enter_new_positions(self, value):
        self.localize(value)
        return True
