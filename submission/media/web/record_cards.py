"""Records the animated video cards and the dashboard scroll with Playwright (1920x1080), then converts each to a
30 fps H.264 clip in submission/media/raw/clips/. Run from the repo root with the media venv:

    submission/media/.venv/bin/python submission/media/web/record_cards.py [v_hook v_title v_arch v_end ch_* dashboard]

The dashboard scene needs a dashboard serving on :8010 (`.venv/bin/python -m uvicorn dashboard.app:app --port 8010`).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
MEDIA = ROOT / 'submission/media'
TPL = MEDIA / 'templates'
RAW = MEDIA / 'raw'
CLIPS = RAW / 'clips'
BUILD = RAW / 'build'
SIZE = {'width': 1920, 'height': 1080}
CARDS = {'v_hook': 6.5, 'v_title': 15.5, 'v_arch': 20.5, 'v_end': 10.5}
CHAPTERS = {'ch_cycle': ('Live on NEAR testnet', 'A full cycle, on-chain.', 'ok'),
            'ch_safety': ('Safety lives in the contract', 'Three safety proofs.', 'bad'),
            'ch_money': ('Paper trades, labelled paper', 'Honest money.', 'warn'),
            'ch_verify': ('No keys. No funds.', 'Verify it yourself.', 'ok')}
CHAPTER_DUR = 1.8


def hook_html() -> str:
    """The hook card, filled only from the real tx 4KxLnhn9 (RPC read) and the on-chain policy (make -s status)."""
    sys.path.insert(0, str(Path(__file__).parent))
    import render as R  # noqa: PLC0415
    t, args, h, at = R.overruled(False)
    pol = R.status(False)['policy']
    reason = next(R.json.loads(lg[11:])['data'][0]['contract_reason'] for lg in R.tx_logs(t) if '"verdict_override"' in lg)
    fill = {'SIDE': args['side'], 'ASK': R.usd(args['notional_usd_cents']), 'ALLOWED': str(args['agent_allowed']).lower(),
            'CAP': R.usd(pol['max_notional_per_trade_usd_cents']), 'REASON': reason, 'TX': R.short(h),
            'WHEN': f'{at:%d %b %Y %H:%M} UTC'}
    src = (TPL / 'v_hook.html').read_text()
    for k, v in fill.items():
        src = src.replace('{{' + k + '}}', R.E(v))
    assert '{{' not in src
    return src


def to_mp4(webm: Path, out: Path, start: float, dur: float) -> None:
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-ss', f'{start:.2f}', '-i', str(webm), '-t', f'{dur:.2f}',
                    '-vf', 'fps=30,format=yuv420p', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-an',
                    str(out)], check=True)
    print(f'{out.relative_to(ROOT)}: {dur:.1f} s')


def record(p, name: str, url: str, dur: float, script=None, viewport=None, scale: float = 1) -> None:
    tmp = RAW / 'pw_video' / name
    shutil.rmtree(tmp, ignore_errors=True)
    b = p.chromium.launch()
    ctx = b.new_context(viewport=viewport or SIZE, device_scale_factor=scale, record_video_dir=str(tmp),
                        record_video_size=SIZE, color_scheme='dark')
    if name == 'dashboard':  # browser zoom 160 % (like Cmd+) from the first paint, for legibility in the video
        ctx.add_init_script("document.addEventListener('DOMContentLoaded', () => document.documentElement.style.zoom = '1.6')")
    t0 = time.monotonic()
    pg = ctx.new_page()
    pg.goto(url)
    pg.evaluate('document.fonts.ready')
    t_load = time.monotonic() - t0
    if script:
        t_load = script(pg, t0) or t_load
    else:
        pg.wait_for_timeout(dur * 1000)
    vid = Path(pg.video.path())
    ctx.close()
    b.close()
    to_mp4(vid, CLIPS / f'{name}.mp4', t_load, dur)


def dashboard(pg, t0: float) -> float:
    """Real dashboard, slow native scroll: journal head, decision #8 (ALLOWED, contract P&L), decision #2 (overridden).
    Returns the time the journal finished loading, so the clip starts on a fully loaded page."""
    pg.wait_for_selector('.panel.decision', timeout=60_000)
    pg.wait_for_function("document.querySelector('#policy') && document.querySelector('#policy').innerText.includes('$2.00')")
    pg.wait_for_timeout(1500)
    start = time.monotonic() - t0
    pg.wait_for_timeout(2500)
    for target in ('#decisions', '.panel.decision >> nth=0', '.panel.decision >> nth=6'):
        pg.locator(target).evaluate("e => e.scrollIntoView({behavior: 'smooth', block: 'start'})")
        pg.wait_for_timeout(4200)
    return start


if __name__ == '__main__':
    CLIPS.mkdir(parents=True, exist_ok=True)
    BUILD.mkdir(parents=True, exist_ok=True)
    for f in ('gallery.css', 'video.css'):
        shutil.copy(TPL / f, BUILD / f)
    targets = sys.argv[1:] or [*CARDS, *CHAPTERS, 'dashboard']
    with sync_playwright() as p:
        for t in targets:
            if t == 'dashboard':
                record(p, 'dashboard', 'http://127.0.0.1:8010/', 15.9, dashboard)
            elif t in CHAPTERS:
                import urllib.parse  # noqa: PLC0415
                shutil.copy(TPL / 'v_chapter.html', BUILD / 'v_chapter.html')
                k, title, rail = CHAPTERS[t]
                q = urllib.parse.urlencode({'k': k, 't': title, 'r': rail})
                record(p, t, (BUILD / 'v_chapter.html').as_uri() + '?' + q, CHAPTER_DUR)
            elif t == 'v_hook':
                (BUILD / 'v_hook.html').write_text(hook_html())
                record(p, t, (BUILD / 'v_hook.html').as_uri(), CARDS[t])
            else:
                shutil.copy(TPL / f'{t}.html', BUILD / f'{t}.html')
                record(p, t, (BUILD / f'{t}.html').as_uri(), CARDS[t])
