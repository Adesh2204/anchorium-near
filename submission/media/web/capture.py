"""Real captures for the gallery, at 2x. Run with the media venv from the repo root while a dashboard is serving:

    .venv/bin/python -m uvicorn dashboard.app:app --port 8010 &
    submission/media/.venv/bin/python submission/media/web/capture.py [dashboard] [nearblocks]

Writes PNGs to submission/media/raw/cap/. Nothing is edited: crops are element screenshots of the live pages.
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'submission/media/raw/cap'
DASH = 'http://127.0.0.1:8010/'
TX_OVERRULED = '4KxLnhn9mdtaQq5wVpxK525yMyBsdpfyKNfqw1aBbAWM'


def dashboard(p) -> None:
    b = p.chromium.launch()
    pg = b.new_page(viewport={'width': 1400, 'height': 1000}, device_scale_factor=2, color_scheme='dark')
    pg.goto(DASH)
    pg.wait_for_selector('.panel.decision', timeout=60_000)
    pg.wait_for_function("document.querySelector('#policy') && document.querySelector('#policy').innerText.includes('$2.00')",
                         timeout=60_000)
    pg.wait_for_timeout(1500)
    pg.screenshot(path=str(OUT / 'dash_full.png'), full_page=True)
    pg.locator('#banner').screenshot(path=str(OUT / 'dash_banner.png'))
    grid = pg.locator('.grid').first
    grid.screenshot(path=str(OUT / 'dash_top_grid.png'))
    for sel in ('#killswitch', '#policy', '#today'):
        pg.locator(sel).locator('xpath=ancestor-or-self::*[contains(@class,"panel")][1]').screenshot(
            path=str(OUT / f'dash_{sel[1:]}.png'))
    cards = pg.locator('.panel.decision')
    for i in range(cards.count()):
        c = cards.nth(i)
        head = c.locator('.dhead').inner_text()
        n = head.split()[0].lstrip('#')
        c.screenshot(path=str(OUT / f'dash_decision_{n}.png'))
    b.close()
    print('dashboard captures ->', OUT.relative_to(ROOT))


def nearblocks(p) -> None:
    b = p.chromium.launch()
    pg = b.new_page(viewport={'width': 1400, 'height': 1000}, device_scale_factor=2, color_scheme='dark')
    pg.goto(f'https://testnet.nearblocks.io/txns/{TX_OVERRULED}', wait_until='domcontentloaded', timeout=90_000)
    pg.wait_for_selector('text=record_decision', timeout=90_000)
    pg.wait_for_timeout(4000)
    pg.screenshot(path=str(OUT / 'nb_overruled_full.png'), full_page=True)
    print('nearblocks page title:', pg.title())
    b.close()


if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True)
    targets = sys.argv[1:] or ['dashboard', 'nearblocks']
    with sync_playwright() as p:
        for t in targets:
            {'dashboard': dashboard, 'nearblocks': nearblocks}[t](p)
