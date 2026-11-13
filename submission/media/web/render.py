"""Builds the Anchorium gallery (gallery spec v2) from real captures. From the repo root, with the media venv:

    submission/media/.venv/bin/python submission/media/web/render.py [--recapture] [g01 ... g11 sheets]

Inputs (all real, under submission/media/raw/, gitignored):
  demo.log, status.log, test_py.log, freeze.log      `make demo`, `make -s status`, `make test-py`, `make freeze`
  contract_test.log                                    `make contract-test` (PATH needs ~/.cargo/bin)
  tx_<hash>.json, agent_keys.json                      keyless NEAR testnet RPC reads
  kill_switch.log                                      web/kill_switch_capture.sh on a MEDIA guard only
  cap/dash_*.png                                       web/capture.py (dashboard element screenshots at 2x)
Text-only facts come from README.md, PROGRESS.md and docs/data/quote_costs_2026-11-12.json, parsed, never retyped.
Every built HTML is scanned for secrets/local paths, and every frame is QA'd (min font 22 px, no overflow, words).
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
MEDIA = ROOT / 'submission/media'
TPL = MEDIA / 'templates'
RAW = MEDIA / 'raw'
CAP = RAW / 'cap'
BUILD = RAW / 'build'
GALLERY = MEDIA / 'gallery'
IST = timezone(timedelta(hours=5, minutes=30))
RPC = 'https://archival-rpc.testnet.fastnear.com'
TEAM_GUARD = 'guard.anchorium-team.testnet'
REPO = 'github.com/Adesh2204/anchorium-near-Paypal'
SECRET_PATTERNS = [r'ed25519:[1-9A-HJ-NP-Za-km-z]{70,}', r'secret_key', r'private_key', r'\bJWT\b',
                   r'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}', r'\.near-credentials', r'/Users/',
                   r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.(com|org|net|io|in)\b']
E = html.escape


# ----- real inputs -----

def run(name: str, cmd: list[str] | str, recapture: bool, env_path: bool = False) -> str:
    out = RAW / f'{name}.log'
    if recapture or not out.exists():
        print(f'capturing {name}', flush=True)
        env = dict(os.environ)
        if env_path:
            env['PATH'] = str(Path.home() / '.cargo/bin') + ':' + env['PATH']
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=900, env=env, shell=isinstance(cmd, str))
        if r.returncode != 0:
            sys.exit(f'{name} failed:\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}')
        out.write_text(r.stdout + r.stderr)
    return out.read_text()


def rpc(method: str, params: dict) -> dict:
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
    r = subprocess.run(['curl', '-s', '--max-time', '30', RPC, '-H', 'Content-Type: application/json', '-d', body],
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout)['result']


def tx(hash_: str, sender: str, recapture: bool) -> dict:
    path = RAW / f'tx_{hash_}.json'
    if recapture or not path.exists():
        path.write_text(json.dumps({'result': rpc('EXPERIMENTAL_tx_status', {'tx_hash': hash_, 'sender_account_id': sender,
                                                                            'wait_until': 'FINAL'})}))
    return json.loads(path.read_text())['result']


def tx_time(t: dict) -> datetime:
    blk = rpc('block', {'block_id': t['transaction_outcome']['block_hash']})
    return datetime.fromtimestamp(blk['header']['timestamp'] / 1e9, timezone.utc)


def tx_logs(t: dict) -> list[str]:
    return [lg for r in t['receipts_outcome'] for lg in r['outcome']['logs']]


def tx_args(t: dict) -> dict:
    return json.loads(base64.b64decode(t['transaction']['actions'][0]['FunctionCall']['args']))


def readme_tx(n: int) -> list[str]:
    row = next(s for s in (ROOT / 'README.md').read_text().splitlines() if s.startswith(f'| {n} |'))
    return re.findall(r'nearblocks\.io/txns/([1-9A-HJ-NP-Za-km-z]{43,44})', row)


def status(recapture: bool) -> dict:
    return json.loads(run('status', ['make', '-s', 'status'], recapture))


def short(h: str) -> str:
    return h[:8] + '…'


def usd(cents: int) -> str:
    return f'${cents / 100:.2f}'


def today() -> str:
    return f'{datetime.now(IST):%d %b %Y}'


# ----- build + QA + shoot -----

def check_secrets(src: str, label: str) -> None:
    for p in SECRET_PATTERNS:
        m = re.search(p, src)
        if m:
            sys.exit(f'{label}: refusing to render, matched {p!r}: {m.group(0)[:24]}')


def build(name: str, f: dict[str, str]) -> Path:
    BUILD.mkdir(parents=True, exist_ok=True)
    shutil.copy(TPL / 'gallery.css', BUILD / 'gallery.css')
    src = (TPL / 'gallery_frame.html').read_text()
    f = {'HEAD_EXTRA': '', 'GLOW': '', **f}
    for k, v in f.items():
        src = src.replace('{{' + k + '}}', v)
    if '{{' in src:
        sys.exit(f'{name}: unfilled placeholder')
    check_secrets(src, name)
    path = BUILD / f'{name}.html'
    path.write_text(src)
    return path


QA_JS = """() => {
  let min = 999, where = '';
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const t = walker.currentNode; if (!t.textContent.trim()) continue;
    const el = t.parentElement; if (el.closest('script,style')) continue;
    const r = el.getBoundingClientRect(); if (!r.width) continue;
    const fs = parseFloat(getComputedStyle(el).fontSize);
    if (fs < min) { min = fs; where = t.textContent.trim().slice(0, 40); }
  }
  const words = [...document.querySelectorAll('.b')].map(e => e.innerText).join(' ').split(/\\s+/).filter(Boolean).length;
  const m = document.querySelector('main');
  return {min, where, words, overflow: m.scrollHeight - m.clientHeight, overflowX: m.scrollWidth - m.clientWidth};
}"""


def shoot(page: Path, out: Path, max_words: int = 30) -> dict:
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={'width': 1800, 'height': 1200}, device_scale_factor=2)
        pg.goto(page.as_uri())
        pg.evaluate('window.__ready')
        missing = pg.evaluate("['Inter','JetBrains Mono'].filter(f => !document.fonts.check('16px \"' + f + '\"'))")
        if missing:
            sys.exit(f'fonts missing: {missing}')
        qa = pg.evaluate(QA_JS)
        big = RAW / f'{out.stem}@2x.png'
        pg.screenshot(path=str(big))
        b.close()
    problems = []
    if qa['min'] < 22:
        problems.append(f'text below 22 px ({qa["min"]} px: {qa["where"]!r})')
    if qa['overflow'] > 1 or qa['overflowX'] > 1:
        problems.append(f'content overflows the content band by {qa["overflow"]} / {qa["overflowX"]} px')
    if qa['words'] > max_words:
        problems.append(f'{qa["words"]} body words > {max_words}')
    Image.open(big).resize((1800, 1200), Image.LANCZOS).save(out)
    subprocess.run(['pngquant', '--force', '--skip-if-larger', '--quality', '85-98', '--output', str(out), str(out)])
    subprocess.run(['oxipng', '-q', '-o', '3', str(out)], check=True)
    size = out.stat().st_size
    assert Image.open(out).size == (1800, 1200) and size < 5 * 1024 * 1024
    print(f'{out.name}: {size // 1024} KB · min font {qa["min"]:.0f} px · {qa["words"]} body words'
          + ('' if not problems else ' · QA FAIL: ' + '; '.join(problems)))
    if problems:
        sys.exit(1)
    return qa


def crop(src: str, box: tuple[int, int, int, int] | None, name: str) -> str:
    """A real 2x element screenshot, optionally cropped (never scaled here); returns its build-relative path."""
    im = Image.open(CAP / src)
    if box:
        im = im.crop(box)
    BUILD.mkdir(parents=True, exist_ok=True)
    im.save(BUILD / name)
    return name


GALLERY_NAMES = {1: '01_cover', 2: '02_problem', 3: '03_architecture', 4: '04_overruled', 5: '05_policy',
                 6: '06_funds_timeline', 7: '07_agent_key', 8: '08_kill_switch', 9: '09_honest_money',
                 10: '10_engineering', 11: '11_verify'}


def frame(n: int, body: str, chip: str, chip_dot: str, rail: str, glow: str = '', glow_xy=(0, 0), head='',
          max_words: int = 30) -> None:
    g = f'<div class="glow {glow}" style="left:{glow_xy[0]}px;top:{glow_xy[1]}px"></div>' if glow else ''
    name = GALLERY_NAMES[n]
    page = build(name, {'N': f'{n:02d}', 'BODY': body, 'CHIP': E(chip), 'CHIP_DOT': chip_dot, 'RAIL': rail, 'GLOW': g,
                        'HEAD_EXTRA': head})
    shoot(page, GALLERY / f'{name}.png', max_words)


def lbl(cid: str, n: int, colour: str, text: str, style: str = '') -> str:
    return f'<div class="lbl b" data-for="{cid}" style="{style}"><span class="n {colour}">{n}</span>{E(text)}</div>'


def under(cid: str, n: int, colour: str, text: str, x: float, width: int = 420, flip: bool = False) -> str:
    """A label whose number sits directly under the marker at x% (so its leader is a straight vertical line).
    flip=True puts the text to the left of the number."""
    if flip:
        return lbl(cid, n, colour, text, f'position:absolute;right:calc(100% - {x}% - 20px);top:0;max-width:{width}px;'
                                         f'flex-direction:row-reverse;text-align:right')
    return lbl(cid, n, colour, text, f'position:absolute;left:calc({x}% - 20px);top:0;max-width:{width}px')


def mk(cid: str, n: int, colour: str, x: float, y: float) -> str:
    return f'<b class="mk {colour}" id="{cid}" style="left:{x}%;top:{y}%">{n}</b>'


# ----- the 11 images -----

def overruled(rc: bool) -> tuple[dict, dict, str, datetime]:
    h = readme_tx(2)[0]
    t = tx(h, 'agent.anchorium-team.testnet', rc)
    return t, tx_args(t), h, tx_time(t)


def g01(rc: bool) -> None:
    t, args, h, at = overruled(rc)
    pol = status(rc)['policy']
    reason = next(json.loads(lg[11:])['data'][0]['contract_reason'] for lg in tx_logs(t) if '"verdict_override"' in lg)
    assert args['agent_allowed'] is True
    body = f"""
    <div class="row" style="flex:1;align-items:center;gap:56px">
      <div class="col" style="width:calc(7 * var(--col) + 6 * 24px)">
        <div class="kicker">NEAR HACKATHON · DEFI · AGENTS</div>
        <div class="headline xl" style="margin-top:22px">Don’t trust the bot. Verify it.</div>
        <div class="row b" style="margin-top:44px;flex-wrap:wrap;gap:16px">
          <span class="tag">Rules in a NEAR contract</span><span class="tag">Every decision on-chain</span>
          <span class="tag">Never holds your money</span></div>
      </div>
      <div class="panel col" style="flex:1;padding:44px 44px 40px;gap:30px;border-color:color-mix(in srgb,var(--bad) 45%,var(--line))">
        <div><div class="b muted" style="font-size:26px">Agent asks</div>
             <div class="mono" style="font-size:60px;font-weight:700">{E(args['side'])} {usd(args['notional_usd_cents'])}</div></div>
        <div><div class="b muted" style="font-size:26px">Policy max</div>
             <div class="mono" style="font-size:60px;font-weight:700">{usd(pol['max_notional_per_trade_usd_cents'])}</div></div>
        <div><div class="b muted" style="font-size:26px">Contract</div>
             <div style="margin-top:8px"><span class="pill bad" style="font-size:56px">REJECTED</span></div>
             <div class="mono bad" style="font-size:40px;font-weight:700;margin-top:14px">{E(reason)}</div></div>
        <div class="mono muted" style="font-size:24px">+ verdict_override event, permanent</div>
      </div>
    </div>"""
    frame(1, body, f'real tx {short(h)} · {TEAM_GUARD} · {at:%d %b %Y}', 'bad', 'bad-ok', 'bad', (1400, 600))


def g02(rc: bool) -> None:
    rows = [('Holds your keys', 'Settles into your own account, no custody'),
            ('Rules live in its own code', 'Rules live in a NEAR contract'),
            ('Breaks rules silently', 'Every decision journaled, overrides flagged'),
            ('Track record = screenshots', 'The contract computes P&L itself'),
            ('Kill switch inside the bot', 'Owner pause in 1 tx + auto-pause at the loss cap')]
    left = ''.join(f'<div class="row b" style="gap:18px;align-items:center;height:124px;border-top:1px solid var(--line)">'
                   f'<span class="bad" style="font-size:40px;font-weight:800;width:40px">✕</span>'
                   f'<span style="font-size:32px">{E(a)}</span></div>' for a, _ in rows)
    right = ''.join(f'<div class="row b" style="gap:18px;align-items:center;height:124px;border-top:1px solid var(--line)">'
                    f'<span class="ok" style="font-size:40px;font-weight:800;width:40px">✓</span>'
                    f'<span style="font-size:32px">{E(b)}</span></div>' for _, b in rows)
    body = f"""
    <div class="headline">Today, you just trust the bot.</div>
    <div class="row" style="margin-top:48px;gap:48px">
      <div class="col" style="flex:1"><div class="kicker" style="margin-bottom:16px">Every bot today</div>{left}</div>
      <div class="col" style="flex:1.25"><div class="kicker" style="margin-bottom:16px;color:var(--ok)">Anchorium</div>{right}</div>
    </div>"""
    # The spec fixes these 5 pairs; they exceed the 30-word guideline (reported at the checkpoint).
    frame(2, body, 'README · Safety model', 'ok', 'muted-ok', 'ok', (1300, 700), max_words=70)


def g03(rc: bool) -> None:
    nodes = [('1', 'Signal', 'frozen strategy', 'OFF-CHAIN · AGENT', 'muted'),
             ('2', 'check_trade', 'guard contract', 'NEAR TESTNET', 'blue'),
             ('3', '1Click quote', 'NEAR Intents', 'NEAR MAINNET', 'ink'),
             ('4', 'record_decision', 'on-chain journal', 'NEAR TESTNET', 'blue'),
             ('5', 'Settle', 'into your account, then record_outcome', 'MAINNET + TESTNET', 'ink')]

    def zone(z: str, kind: str) -> str:
        st = {'blue': 'color:var(--blue);border-color:var(--blue);background:color-mix(in srgb,var(--blue) 12%,transparent)',
              'ink': 'color:var(--ink);border-color:var(--ink);background:rgba(232,235,239,.08)',
              'muted': 'color:var(--muted);border-color:var(--line)'}[kind]
        return (f'<div class="mono" style="margin-top:18px;font-size:22px;font-weight:700;border:2px solid;border-radius:8px;'
                f'padding:6px 10px;text-align:center;{st}">{z}</div>')
    cells = []
    for i, (n, a, b, z, kind) in enumerate(nodes):
        cells.append(f'<div class="col" style="flex:1"><div class="panel" style="padding:30px 20px;min-height:300px;'
                     f'{"border:2px solid var(--ok);" if n == "2" else ""}">'
                     f'<div class="mono muted" style="font-size:24px">{n}</div>'
                     f'<div class="mono" style="font-size:26px;font-weight:800;margin-top:10px;white-space:nowrap">{E(a)}</div>'
                     f'<div class="b muted" style="font-size:26px;margin-top:14px;line-height:1.35">{E(b)}</div></div>{zone(z, kind)}</div>')
        if i < len(nodes) - 1:
            cells.append('<div class="muted" style="font-size:40px;align-self:flex-start;margin-top:120px">→</div>')
    owner = """
      <div class="row" style="margin-top:90px;height:150px;position:relative">
        <div class="col" style="position:absolute;left:calc(20% + 8px);width:calc(20% - 30px);align-items:center;height:150px">
          <div class="panel mono" style="padding:12px 24px;font-size:28px;font-weight:800;border:2px solid var(--bad)">OWNER</div>
          <div style="width:3px;flex:1;background:var(--bad);margin-top:6px"></div>
          <div class="bad" style="font-size:26px;line-height:1;margin-top:-6px">▼</div>
          <div class="mono bad" style="position:absolute;left:calc(50% + 22px);top:84px;font-size:24px;white-space:nowrap">pause / update_policy</div>
        </div>
      </div>"""
    body = f"""
    <div class="headline">The rules live on-chain, not in the bot.</div>
    {owner}
    <div class="row" style="gap:14px;align-items:flex-start">{''.join(cells)}</div>"""
    frame(3, body, 'README · Architecture', 'ok', 'ok', 'ok', (560, 760))


def g04(rc: bool) -> None:
    t, args, h, at = overruled(rc)
    pol = status(rc)['policy']
    vo = next(lg for lg in tx_logs(t) if '"verdict_override"' in lg)
    card = crop('dash_decision_2.png', None, 'c04_card.png')
    line = E(vo).replace('verdict_override', '<span class="hl bad">verdict_override</span>')
    body = f"""
    <div class="kicker">PROOF 01 · THE CONTRACT OUTRANKS THE BOT</div>
    <div class="headline md">The bot tried to cheat. The contract said no.</div>
    <div class="shot" style="margin-top:34px">{mk('a1', 1, 'bad', 20.95, 59)}{mk('a2', 2, 'bad', 40.6, 74)}
      <img src="{card}"></div>
    <div style="position:relative;height:52px;margin:34px 0 28px">
      {under('a1', 1, 'bad', 'Agent claimed: allowed', 20.95, flip=True)}
      {under('a2', 2, 'bad', f'Contract: {usd(args["notional_usd_cents"])} > {usd(pol["max_notional_per_trade_usd_cents"])} cap', 40.6, 560)}</div>
    <div class="panel term"><div class="bar"><i></i><i></i><i></i><span>NEAR testnet RPC · tx {short(h)} · contract log</span></div>
      <pre style="font-size:24px;position:relative;padding-left:76px"><b class="mk bad" id="a3" style="left:36px;top:44px">3</b>{line}</pre></div>
    <div style="position:relative;height:52px;margin-top:26px">{lbl('a3', 3, 'bad', 'verdict_override, on-chain forever', 'position:absolute;left:16px;top:0')}</div>"""
    frame(4, body, f'real tx {short(h)} · {at:%d %b %Y %H:%M} UTC · {TEAM_GUARD}', 'bad', 'bad', 'bad', (500, 520))


def g05(rc: bool) -> None:
    pol = status(rc)['policy']
    grid = Image.open(CAP / 'dash_top_grid.png')
    w = int(grid.width * 1325 / 2000)  # the KILL SWITCH and POLICY panels; TODAY is cut off
    img = crop('dash_top_grid.png', (0, 0, w, grid.height), 'c05_panels.png')
    upd = tx('DExrWkb3p8zQauc31dsqhCA6M9jfbWvd4YLWh4XX75df', 'anchorium-team.testnet', rc)
    assert any('policy_updated' in lg for lg in tx_logs(upd))
    body = f"""
    <div class="kicker">PROOF 02 · THE OWNER'S RULES ARE ON-CHAIN</div>
    <div class="headline">The rules are code anyone can read.</div>
    <div class="row" style="margin-top:40px;gap:72px;align-items:center;flex:1">
      <div class="col" style="width:1120px"><div class="shot">{mk('p1', 1, 'ok', 102.6, 24)}{mk('p2', 2, 'ok', 102.6, 44)}{mk('p3', 3, 'ok', -0.6, 38)}
        <img src="{img}"></div>
        <div style="position:relative;height:52px;margin-top:34px">{lbl('p3', 3, 'ok', 'Only the owner can change these', 'position:absolute;left:-20px;top:0')}</div></div>
      <div class="col" style="flex:1;gap:40px">
        {lbl('p1', 1, 'ok', f'{usd(pol["max_notional_per_trade_usd_cents"])} max per trade')}
        {lbl('p2', 2, 'ok', f'{usd(pol["max_daily_loss_usd_cents"])} daily loss → auto-pause')}
        <div class="muted" style="font-size:24px;line-height:1.45;margin-top:10px"><span class="b">Even rule changes are public:</span>
          <span class="mono" style="color:var(--ink)">100 → 150 bps</span><br><span class="mono">policy_updated · tx DExrWkb3…</span></div>
      </div>
    </div>"""
    frame(5, body, f'make status · read from {TEAM_GUARD} · {today()}', 'ok', 'ok', 'ok', (560, 640))


def g06(rc: bool) -> None:
    dec, out = readme_tx(8)
    card = crop('dash_decision_8.png', None, 'c06_card.png')
    steps = ['Signal', 'Guard verdict', '1Click quote', 'Where the funds are', 'Outcome']
    stepper = ''.join(f'<div class="row" style="flex:1;align-items:center;gap:10px"><span class="mono muted" style="font-size:24px">{i + 1}</span>'
                      f'<span class="b" style="font-size:26px;font-weight:700;white-space:nowrap">{E(s)}</span>'
                      + ('<span class="muted" style="font-size:26px;margin-left:auto;margin-right:14px">→</span>' if i < 4 else '')
                      + '</div>' for i, s in enumerate(steps))
    body = f"""
    <div class="row" style="align-items:flex-end;justify-content:space-between">
      <div><div class="kicker">PROOF 03 · ALWAYS KNOW WHERE YOUR MONEY IS</div>
      <div class="headline">Every decision, fully traceable.</div></div>
      <span class="label-paper" style="margin-bottom:14px">DRY RUN</span></div>
    <div class="col" style="flex:1;justify-content:center">
    <div class="row" style="padding:0 0 18px 26px">{stepper}</div>
    <div class="shot">{mk('f1', 1, 'ok', 20.3, 42)}{mk('f2', 2, 'ok', 39.9, 65)}{mk('f3', 3, 'ok', 59.6, 42)}{mk('f4', 4, 'ok', 79.2, 42)}
      <img src="{card}"></div>
    <div style="position:relative;height:90px;margin-top:44px">
      {under('f1', 1, 'ok', 'contract said OK', 20.3, 300)}{under('f2', 2, 'ok', 'worst-case slippage checked', 39.9, 300)}
      {under('f3', 3, 'ok', 'funds never left your account', 59.6, 300)}{under('f4', 4, 'ok', 'P&L computed by the contract', 79.2, 300)}</div></div>"""
    frame(6, body, f'decision tx {short(dec)} · outcome tx {short(out)} · 11 Nov 2026 · DRY RUN', 'ok', 'ok', 'ok', (900, 700))


def g07(rc: bool) -> None:
    path = RAW / 'agent_keys.json'
    if rc or not path.exists():
        path.write_text(json.dumps({'result': rpc('query', {'request_type': 'view_access_key_list', 'finality': 'final',
                                                            'account_id': 'agent.anchorium-team.testnet'})}))
    keys = json.loads(path.read_text())['result']['keys']
    assert len(keys) == 1 and 'FunctionCall' in keys[0]['access_key']['permission']
    fc = keys[0]['access_key']['permission']['FunctionCall']
    out = []
    for s in json.dumps(keys, indent=2).splitlines():
        e = E(s)
        if any(k in s for k in ('"receiver_id"', '"record_decision"', '"record_outcome"', '"method_names"')):
            e = f'<span class="hl ok">{e}</span>'
        out.append(e)
    dele = tx('86WYN7RoR1PNERjpSzspQXJ6uDX2fqnGP4m3LLrGyPq8', 'agent.anchorium-team.testnet', rc)
    assert list(dele['transaction']['actions'][0]) == ['DeleteKey']
    sandbox = (ROOT / 'contracts/anchorium-guard/tests/sandbox.rs').read_text()
    assert 'async fn function_call_key_can_journal_but_nothing_else' in sandbox
    can = ''.join(f'<div class="mono" style="font-size:30px;margin-top:12px">✓ {E(m)}</div>' for m in fc['method_names'])
    cannot = ''.join(f'<div class="b" style="font-size:30px;margin-top:12px">✕ {E(m)}</div>'
                     for m in ('move NEAR', 'pause', 'change rules', 'call any other contract'))
    body = f"""
    <div class="kicker">PROOF 04 · THE BOT'S KEY CAN ONLY WRITE THE JOURNAL</div>
    <div class="headline">A key that can only do one thing.</div>
    <div class="row" style="margin-top:36px;gap:48px;flex:1;min-height:0">
      <div class="panel term" style="width:1000px"><div class="bar"><i></i><i></i><i></i>
        <span>view_access_key_list agent.anchorium-team.testnet</span></div>
        <pre style="font-size:22px;line-height:1.38;padding:16px 28px">{chr(10).join(out)}</pre></div>
      <div class="col" style="flex:1;gap:30px">
        <div><div class="kicker" style="color:var(--ok)">CAN</div><div class="ok">{can}</div></div>
        <div><div class="kicker" style="color:var(--bad)">CANNOT</div><div class="bad">{cannot}</div></div>
        <div class="muted" style="font-size:22px;line-height:1.5"><span class="b">We deleted its full-access key ourselves</span>
          <span class="mono"> · tx 86WYN7Ro…</span><br><span class="mono" style="overflow-wrap:anywhere">sandbox test: function_call_key_can_journal_but_nothing_else</span></div>
      </div>
    </div>"""
    frame(7, body, f'keyless RPC query · NEAR testnet · {today()}', 'ok', 'ok', 'ok', (520, 700))


def g08(rc: bool) -> None:
    log = (RAW / 'kill_switch.log').read_text()
    guard = re.search(r'on (guard\.anchorium-media-\d+\.testnet)', log).group(1)
    owner = guard.removeprefix('guard.')
    verdicts = re.findall(r'"allowed": (true|false),\s*"reason": "([A-Z_]+)"', log)
    txs = re.findall(r'Transaction ID: ([1-9A-HJ-NP-Za-km-z]{43,44})', log)
    events = re.findall(r'"event":"(paused|resumed)"', log)
    assert verdicts == [('true', 'OK'), ('false', 'PAUSED'), ('true', 'OK')] and len(txs) == 2 and events == ['paused', 'resumed']
    req = re.search(r'check_trade (BUY wNEAR \$[\d.]+)', log).group(1)

    def card(colour: str, top: str, big: str, sub: str) -> str:
        return (f'<div class="panel col" style="flex:1;padding:34px;gap:18px;border:2px solid var(--{colour})">'
                f'<div class="mono muted" style="font-size:24px">{top}</div>'
                f'<div style="font-size:30px" class="mono">{big}</div><div class="mono muted" style="font-size:22px">{sub}</div></div>')
    arrow = '<div class="muted" style="font-size:52px;align-self:center">→</div>'
    body = f"""
    <div class="kicker">PROOF 05 · THE OWNER'S KILL SWITCH</div>
    <div class="headline">One transaction stops every new trade.</div>
    <div class="sub">On <span class="mono" style="color:var(--ink)">{guard}</span>, a fresh guard deployed for this demo.</div>
    <div class="row" style="margin-top:36px;gap:24px">
      {card('ok', 'BEFORE · check_trade', f'{E(req)}<br><span class="pill ok" style="font-size:52px;display:inline-block;margin-top:14px">OK</span>', 'allowed: true')}
      {arrow}
      {card('bad', 'OWNER · 1 transaction', '<span class="pill bad" style="font-size:52px">pause</span>', f'tx {short(txs[0])} · event: paused')}
      {arrow}
      {card('bad', 'AFTER · check_trade', f'{E(req)}<br><span class="pill bad" style="font-size:52px;display:inline-block;margin-top:14px">PAUSED</span>', 'allowed: false')}
    </div>
    <div class="panel" style="margin-top:28px;padding:22px 30px;font-size:28px">
      <span class="b">Exits stay possible · the contract also pauses itself at the $1 daily loss cap</span>
      <span class="mono muted" style="font-size:24px">(sandbox test)</span> · <span class="b">owner</span> <span class="mono">resume</span>
      <span class="mono muted" style="font-size:24px">tx {short(txs[1])}</span> <span class="b">→ OK again</span></div>"""
    frame(8, body, f'pause tx {short(txs[0])} · resume tx {short(txs[1])} · {owner} · {today()}',
          'bad', 'bad', 'bad', (1300, 640), max_words=34)


def g09(rc: bool) -> None:
    readme = (ROOT / 'README.md').read_text()
    m = re.search(r'gross \+\$([\d.]+) against the market price, fee −\$([\d.]+), spread −\$([\d.]+), gas −\$([\d.]+), net\s+'
                  r'\*\*\+\$([\d.]+) \(\+₹([\d.]+)', readme)
    g_s, f_s, s_s, ga_s, n_s, i_s = m.groups()
    gross, fee, spread, gas, net = (float(x) for x in (g_s, f_s, s_s, ga_s, n_s))  # bar geometry only
    l1 = re.search(r'net result was \*\*−\$([\d.]+) \(−₹([\d.]+)', readme)
    assert abs(gross - fee - spread - gas - net) < 1e-9
    W, H, top, base = 1000, 516, 60, 430
    k = (base - top) / gross
    bars = [('Gross move', 0, gross, 'ok', f'+${g_s}'), ('1Click fee', gross - fee, gross, 'bad', f'−${f_s}'),
            ('Spread', gross - fee - spread, gross - fee, 'bad', f'−${s_s}'),
            ('Gas', net, net + gas, 'bad', f'−${ga_s}'), ('Net', 0, net, 'net', f'+${n_s}')]
    bw, gap, x0 = 150, 50, 20
    svg = [f'<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" style="overflow:visible">',
           f'<line x1="0" y1="{base}" x2="{W}" y2="{base}" stroke="#283039" stroke-width="2"/>']
    for i, (lab, lo, hi, kind, txt) in enumerate(bars):
        x = x0 + i * (bw + gap)
        y1, y2 = base - hi * k, base - lo * k
        col = '#f97066' if kind == 'bad' else '#4cc38a'
        fill = 'rgba(76,195,138,.18)' if kind == 'net' else col
        svg.append(f'<rect x="{x}" y="{y1:.1f}" width="{bw}" height="{max(y2 - y1, 3):.1f}" rx="6" fill="{fill}" '
                   f'stroke="{col}" stroke-width="{4 if kind == "net" else 0}"/>')
        svg.append(f'<text x="{x + bw / 2}" y="{y1 - 14:.1f}" fill="{col}" font-family="JetBrains Mono" font-size="28" '
                   f'font-weight="700" text-anchor="middle">{txt}</text>')
        svg.append(f'<text x="{x + bw / 2}" y="{base + 40}" fill="#98a1ad" font-family="Inter" font-size="24" '
                   f'text-anchor="middle">{lab}</text>')
        if i < len(bars) - 2:
            nxt = base - (lo if kind == 'bad' else hi) * k
            svg.append(f'<line x1="{x + bw}" y1="{nxt:.1f}" x2="{x + bw + gap}" y2="{nxt:.1f}" stroke="#98a1ad" '
                       f'stroke-width="2" stroke-dasharray="6 6"/>')
    svg.append('</svg>')
    head = ('<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">'
            '<script src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>'
            '<script>window.__beforeReady = async () => { katex.render(String.raw`\\text{exit when }(P-E-G)\\;-\\;'
            '0.312\\cdot\\max(P-E,\\,0)\\;\\ge\\;\\text{INR }1.00`, document.getElementById("formula"), '
            '{throwOnError: true}); await document.fonts.ready; };</script>')
    body = f"""
    <div class="row" style="align-items:flex-end;justify-content:space-between">
      <div class="headline">Every fee shown. Wins and losses.</div>
      <span class="label-paper" style="margin-bottom:14px">PAPER · real 1Click quotes, simulated fill</span></div>
    <div class="row" style="margin-top:36px;gap:48px;align-items:flex-start">
      <div class="col" style="width:1040px"><div class="mono muted" style="font-size:24px;margin-bottom:20px">Paper trade 2 · NEAR · one $2 round trip</div>
        {''.join(svg)}
        <div class="mono ok" style="font-size:30px;font-weight:700;margin-top:10px">Net +${n_s} = +INR {i_s}</div></div>
      <div class="panel col" style="flex:1;padding:36px;gap:16px;border:2px solid var(--bad)">
        <div class="mono muted" style="font-size:24px">Paper trade 1 · BTC</div>
        <div class="mono bad" style="font-size:64px;font-weight:800">−INR {l1.group(2)}</div>
        <div class="mono bad" style="font-size:28px">−${l1.group(1)}</div>
        <div class="b" style="font-size:28px">Costs beat the move. Shown anyway.</div></div>
    </div>
    <div class="row" style="margin-top:auto;align-items:center;gap:36px">
      <div id="formula" style="font-size:30px"></div>
      <div class="b muted" style="font-size:24px">31.2% tax reserved before any profit is called a profit</div></div>"""
    frame(9, body, 'docs/paper_trade_profit_report.md · docs/paper_trade_report.md', 'warn', 'warn', 'warn', (560, 640),
          head=head)


def g10(rc: bool) -> None:
    py = re.search(r'(\d+) passed', run('test_py', ['make', 'test-py'], rc).strip().splitlines()[-1]).group(1)
    ct = run('contract_test', 'make contract-test', rc, env_path=True)
    unit, sand = re.findall(r'test result: ok\. (\d+) passed', ct)[:2]
    fzt = run('freeze', ['make', 'freeze'], rc)
    fz = json.loads(fzt[fzt.index('{'):])
    vend = fz['vendored_verify_strategy_freeze']
    assert vend['passed'] and fz['passed']
    params = vend['parameter_count']
    prog = (ROOT / 'PROGRESS.md').read_text()
    rev = re.search(r'independent review .*?: (\d+) real findings, all fixed', prog).group(1)
    assert '1. CRITICAL' in prog
    samples = len(json.loads((ROOT / 'docs/data/quote_costs_2026-11-12.json').read_text())['samples'])
    money_files = ['anchorium/money.py', 'anchorium/agent/orchestrator.py', 'anchorium/paper_trade.py',
                   'anchorium/near/live.py', 'anchorium/live_exit.py']
    floats = sum(len(re.findall(r'\bfloat\b', (ROOT / f).read_text())) for f in money_files)
    stats = [(py, 'Python tests', 'ok'), (f'{unit} + {sand}', 'contract unit + sandbox tests', 'ok'),
             (f'{params}/{params}', 'frozen strategy parameters verified', 'ok'),
             (rev, 'review findings fixed (1 critical)', 'ok'),
             (str(samples), 'real 1Click round trips measured', 'blue'), (str(floats), 'floats in money maths', 'ok')]
    grid = ''.join(f'<div class="stat" style="width:calc((100% - 48px) / 3)"><div class="v {c}">{E(v)}</div>'
                   f'<div class="k b">{E(k)}</div></div>' for v, k, c in stats)
    strip = ' · '.join([f'{py} passed', f'{unit} passed', f'{sand} passed', '"passed": true'])
    body = f"""
    <div class="headline">Built like it handles real money.</div>
    <div class="row" style="margin-top:56px;flex-wrap:wrap;gap:52px 24px">{grid}</div>
    <div class="panel term" style="margin-top:auto"><pre class="ok" style="font-size:26px;padding:18px 28px">{E(strip)}</pre></div>"""
    frame(10, body, f'make test-py · make contract-test · make freeze · {today()}', 'ok', 'ok', 'ok', (900, 560),
          max_words=34)


def g11(rc: bool) -> None:
    import qrcode  # noqa: PLC0415
    import qrcode.image.svg  # noqa: PLC0415
    svg = qrcode.make(f'https://{REPO}', image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=1).to_string(
        encoding='unicode')
    svg = re.sub(r'<\?xml[^>]*>', '', svg).replace('fill="#000000"', 'fill="#e8ebef"')
    svg = re.sub(r'<svg ', '<svg style="width:256px;height:256px;display:block" ', svg, count=1)
    lic = (ROOT / 'LICENSE').read_text().splitlines()[0]
    assert lic.startswith('MIT')
    steps = [f'git clone {REPO}', 'make setup', 'make demo']
    st = ''.join(f'<div class="row" style="gap:26px;align-items:baseline;margin-top:20px"><span class="mono muted" style="font-size:36px">{i + 1}</span>'
                 f'<span class="mono" style="font-size:42px;font-weight:700">{E(s)}</span></div>' for i, s in enumerate(steps))
    see = ' · '.join(['real check_trade calls', 'real 1Click quotes', 'the compromised agent rejected', 'the live on-chain journal'])
    body = f"""
    <div class="headline md">Don’t trust us either. Verify in one minute.</div>
    <div class="row" style="margin-top:20px;gap:40px;align-items:flex-start">
      <div class="col" style="flex:1">{st}
        <div class="b" style="margin-top:34px;font-size:28px;line-height:1.5"><span class="ok" style="font-weight:700">You’ll see:</span> {E(see)}</div></div>
      <div class="panel" style="padding:20px;margin-top:22px">{svg}</div>
    </div>
    <div class="row" style="margin-top:auto;gap:40px;align-items:baseline;flex-wrap:wrap">
      <span class="mono" style="font-size:32px;font-weight:700">{REPO}</span>
      <span class="mono blue" style="font-size:32px;font-weight:700">{TEAM_GUARD}</span></div>
    <div class="b muted" style="margin-top:16px;font-size:26px">Built at NEAR HACKATHON · Adesh &amp; Parth · {E(lic.split()[0])}</div>"""
    frame(11, body, 'docs/JUDGES.md · no keys, no funds', 'ok', 'ok', 'ok', (1500, 640), max_words=34)


def sheets(_: bool) -> None:
    files = [GALLERY / f'{n}.png' for n in GALLERY_NAMES.values()]
    tw, th, gap, pad = 420, 280, 24, 36
    sheet = Image.new('RGB', (pad * 2 + 4 * tw + 3 * gap, pad * 2 + 3 * th + 2 * gap), (15, 18, 22))
    for i, f in enumerate(files):
        sheet.paste(Image.open(f).convert('RGB').resize((tw, th), Image.LANCZOS),
                    (pad + (i % 4) * (tw + gap), pad + (i // 4) * (th + gap)))
    sheet.save(GALLERY / '_contact_sheet.png')
    tw, th, gap = 450, 300, 12  # 25 %: about the Devpost thumbnail size
    thumb = Image.new('RGB', (4 * tw + 3 * gap, 3 * th + 2 * gap), (40, 44, 52))
    for i, f in enumerate(files):
        thumb.paste(Image.open(f).convert('RGB').resize((tw, th), Image.LANCZOS), ((i % 4) * (tw + gap), (i // 4) * (th + gap)))
    thumb.save(GALLERY / '_thumbnail_test.png')
    for f in ('_contact_sheet.png', '_thumbnail_test.png'):
        subprocess.run(['oxipng', '-q', '-o', '2', str(GALLERY / f)], check=True)
    print('wrote _contact_sheet.png and _thumbnail_test.png')


TARGETS = {f'g{n:02d}': fn for n, fn in zip(range(1, 12), (g01, g02, g03, g04, g05, g06, g07, g08, g09, g10, g11))}
TARGETS['sheets'] = sheets

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('targets', nargs='*', default=list(TARGETS))
    ap.add_argument('--recapture', action='store_true')
    a = ap.parse_args()
    GALLERY.mkdir(parents=True, exist_ok=True)
    for t in a.targets:
        TARGETS[t](a.recapture)
