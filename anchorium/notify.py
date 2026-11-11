"""Gets a human's attention when a LIVE leg waits for their typed confirmation (macOS notification + sound)."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def notify(title: str, message: str, *, sound: str = 'Glass', repeat: int = 3) -> None:
    """Terminal bell, a macOS notification and a repeated sound. Never raises: a missing notifier must not
    affect the trade."""
    print('\a', end='', flush=True)
    if sys.platform != 'darwin':
        return
    try:
        script = f'display notification {json.dumps(message)} with title {json.dumps(title)} sound name "{sound}"'
        subprocess.Popen(['osascript', '-e', script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.Popen(['sh', '-c', f'for i in $(seq {int(repeat)}); do afplay /System/Library/Sounds/{sound}.aiff; done'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def deadline_text(deadline_iso: str | None, now: datetime | None = None) -> str:
    """'HH:MM:SS IST (NNN s left)' for a 1Click quote deadline."""
    if not deadline_iso:
        return 'no deadline in the quote'
    deadline = datetime.fromisoformat(deadline_iso.replace('Z', '+00:00'))
    left = int((deadline - (now or datetime.now(timezone.utc))).total_seconds())
    return f'{deadline.astimezone(IST):%H:%M:%S} IST ({left} s left)'


def notifying_confirm(confirm, label: str, notifier=notify):
    """Wraps the typed confirmation: alert the human (with the quote deadline) right before the prompt."""
    def wrapped(phrase, details=None, **kw):
        when = deadline_text((details or {}).get('quote deadline'))
        print(f'\n>>> {label}: your confirmation is needed. Quote deadline {when}', flush=True)
        notifier(f'Anchorium LIVE {label}', f'Type the confirmation in your terminal before {when}')
        shown = dict(details or {})
        if shown.get('quote deadline'):
            shown['quote deadline'] = f'{shown["quote deadline"]} = {when}'
        return confirm(phrase, shown, **kw)
    return wrapped
