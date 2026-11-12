"""Records the animated video cards and the dashboard scroll with Playwright (1920x1080), then converts each to a
30 fps H.264 clip in submission/media/raw/clips/. Run from the repo root with the media venv:

    submission/media/.venv/bin/python submission/media/web/record_cards.py [v_title v_arch v_end dashboard]

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
CARDS = {'v_title': 15.5, 'v_arch': 20.5, 'v_end': 10.5}


def to_mp4(webm: Path, out: Path, start: float, dur: float) -> None:
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-ss', f'{start:.2f}', '-i', str(webm), '-t', f'{dur:.2f}',
                    '-vf', 'fps=30,format=yuv420p', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-an',
                    str(out)], check=True)
    print(f'{out.relative_to(ROOT)}: {dur:.1f} s')


def record(p, name: str, url: str, dur: float, script=None) -> None:
    tmp = RAW / 'pw_video' / name
    shutil.rmtree(tmp, ignore_errors=True)
    b = p.chromium.launch()
    ctx = b.new_context(viewport=SIZE, record_video_dir=str(tmp), record_video_size=SIZE, color_scheme='dark')
    t0 = time.monotonic()
    pg = ctx.new_page()
    pg.goto(url)
    pg.evaluate('document.fonts.ready')
    t_load = time.monotonic() - t0
    if script:
        script(pg)
    else:
        pg.wait_for_timeout(dur * 1000)
    vid = Path(pg.video.path())
    ctx.close()
    b.close()
    to_mp4(vid, CLIPS / f'{name}.mp4', t_load, dur)


def dashboard(pg) -> None:
    """Slow, real scroll: banner + policy, then the journal down to decision #8 (ALLOWED) and #2 (overridden)."""
    pg.wait_for_selector('.panel.decision', timeout=60_000)
    pg.wait_for_timeout(2500)
    for target in ('#decisions', '.panel.decision >> nth=0', '.panel.decision >> nth=6'):
        el = pg.locator(target)
        y = el.evaluate('e => e.getBoundingClientRect().top + window.scrollY - 140')
        pg.evaluate(f'''() => new Promise(r => {{ const s = window.scrollY, d = {y} - s, t0 = performance.now();
            function f(t) {{ const k = Math.min(1, (t - t0) / 1600); window.scrollTo(0, s + d * (1 - Math.pow(1 - k, 3)));
              k < 1 ? requestAnimationFrame(f) : r(); }} requestAnimationFrame(f); }})''')
        pg.wait_for_timeout(3500)


if __name__ == '__main__':
    CLIPS.mkdir(parents=True, exist_ok=True)
    BUILD.mkdir(parents=True, exist_ok=True)
    for f in ('gallery.css', 'video.css'):
        shutil.copy(TPL / f, BUILD / f)
    targets = sys.argv[1:] or [*CARDS, 'dashboard']
    with sync_playwright() as p:
        for t in targets:
            if t == 'dashboard':
                record(p, 'dashboard', 'http://127.0.0.1:8010/', 18.0, dashboard)
            else:
                shutil.copy(TPL / f'{t}.html', BUILD / f'{t}.html')
                record(p, t, (BUILD / f'{t}.html').as_uri(), CARDS[t])
