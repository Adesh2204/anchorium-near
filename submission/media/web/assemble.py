"""Assembles the silent demo video (no voiceover; on-screen captions carry the story) from the real captures.

    submission/media/.venv/bin/python submission/media/web/assemble.py

Inputs: submission/media/raw/*.mp4 + *_end.png (VHS), raw/clips/*.mp4 (record_cards.py), gallery/*.png.
Outputs: submission/media/video/anchorium_demo_full.mp4, anchorium_demo_60s.mp4, captions.srt.
Nothing is altered: footage is trimmed, sped up (with a visible badge) or held on its real last frame; highlight boxes
and captions are drawn on top. Coordinates of the boxes refer to the 1920x1080 VHS frames.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
MEDIA = ROOT / 'submission/media'
RAW = MEDIA / 'raw'
CLIPS = RAW / 'clips'
SEG = RAW / 'segments'
OVL = RAW / 'overlays'
BUILD = RAW / 'build'
OUT = MEDIA / 'video'
FPS = 30
BG = '0x0f1216'
STAGE = (192, 84, 1536, 864)          # x, y, w, h of real footage (16:9) inside the 1920x1080 frame
STILL32 = (312, 84, 1296, 864)        # gallery stills (3:2)
COL = {'ok': '0x4cc38a', 'bad': '0xf97066', 'warn': '0xf5b546', 'blue': '0x7c9cff'}


@dataclass
class Seg:
    name: str
    kind: str                         # card | clip | still
    src: str
    dur: float
    start: float = 0.0
    speed: float = 1.0
    label: str = ''
    dot: str = 'muted'
    badge: str = ''
    caps: list = field(default_factory=list)      # (t0, t1, text)
    boxes: list = field(default_factory=list)     # (x, y, w, h, colour, t_from)
    zoom: tuple = (1.0, 1.0, None)                # (z0, z1, (cx, cy) in source px)
    fit: str = 'stage'                            # stage | still32 | full


def ff(*args: str) -> None:
    r = subprocess.run(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', *args], capture_output=True, text=True)
    if r.returncode:
        sys.exit(r.stderr[-3000:])


def overlay_png(name: str, label: str = '', dot: str = 'muted', badge: str = '', cap: str = '', page=None) -> Path:
    out = OVL / f'{name}.png'
    q = urllib.parse.urlencode({k: v for k, v in {'label': label, 'dot': dot, 'badge': badge, 'cap': cap}.items() if v})
    page.goto((BUILD / 'v_overlay.html').as_uri() + '?' + q)
    page.evaluate('document.fonts.ready')
    page.screenshot(path=str(out), omit_background=True)
    return out


def build_segment(s: Seg, page) -> Path:
    out = SEG / f'{s.name}.mp4'
    if s.kind == 'card':
        ff('-ss', str(s.start), '-t', str(s.dur), '-i', str(CLIPS / s.src),
           '-vf', f'fps={FPS},scale=1920:1080,format=yuv420p', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20',
           '-an', str(out))
        return out
    inputs, chains = [], []
    x, y, w, h = {'stage': STAGE, 'still32': STILL32, 'full': (0, 0, 1920, 1080)}[s.fit]
    boxes = ','.join(f"drawbox=x={bx}:y={by}:w={bw}:h={bh}:color={COL[c]}@0.95:t=5:enable='gte(t,{t0})'"
                     for bx, by, bw, bh, c, t0 in s.boxes)
    if s.kind == 'clip':
        inputs += ['-ss', str(s.start), '-t', str(s.dur * s.speed), '-i', str(RAW / s.src)]
        pre = f'setpts=(PTS-STARTPTS)/{s.speed},fps={FPS}' + (',' + boxes if boxes else '')
        chains.append(f'[0:v]{pre},scale={w}:{h}:flags=lanczos[fg]')
    else:  # still with an optional slow zoom towards (cx, cy)
        src = (MEDIA / s.src) if s.src.startswith('gallery/') else (RAW / s.src)
        inputs += ['-loop', '1', '-framerate', str(FPS), '-t', str(s.dur), '-i', str(src)]
        z0, z1, c = s.zoom
        n = int(round(s.dur * FPS))
        probe = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                                           'stream=width,height', '-of', 'json', str(src)],
                                          capture_output=True, text=True).stdout)['streams'][0]
        iw, ih = probe['width'], probe['height']
        cx, cy = c if c else (iw / 2, ih / 2)
        up = 3
        z = f'{z0}+({z1}-{z0})*on/{max(n - 1, 1)}'
        zx = f"max(0,min({cx * up}-iw/zoom/2,iw-iw/zoom))"
        zy = f"max(0,min({cy * up}-ih/zoom/2,ih-ih/zoom))"
        pre = (boxes + ',' if boxes else '') + f'scale={iw * up}:{ih * up}:flags=lanczos'
        chains.append(f"[0:v]{pre},zoompan=z='{z}':x='{zx}':y='{zy}':d=1:s={w}x{h}:fps={FPS}[fg]")
    chains.append(f'color=c={BG}:s=1920x1080:r={FPS}:d={s.dur}[bg0]')
    chains.append(f'[bg0]drawbox=x={x - 1}:y={y - 1}:w={w + 2}:h={h + 2}:color=0x283039:t=2[bg]')
    chains.append(f'[bg][fg]overlay={x}:{y}:shortest=1[v0]')
    last, k = 'v0', 1
    ovls = [(overlay_png(f'{s.name}_base', s.label, s.dot, s.badge, '', page), 0, s.dur)]
    for i, (t0, t1, text) in enumerate(s.caps):
        ovls.append((overlay_png(f'{s.name}_cap{i}', cap=text, page=page), t0, t1))
    for png, t0, t1 in ovls:
        inputs += ['-loop', '1', '-framerate', str(FPS), '-t', str(s.dur), '-i', str(png)]
        chains.append(f"[{last}][{k}:v]overlay=0:0:enable='between(t,{t0},{t1})'[v{k}]")
        last, k = f'v{k}', k + 1
    chains.append(f'[{last}]format=yuv420p[out]')
    ff(*inputs, '-filter_complex', ';'.join(chains), '-map', '[out]', '-t', str(s.dur), '-r', str(FPS),
       '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-an', str(out))
    return out


MEDIA_GUARD = 'guard.anchorium-media-19428.testnet'
VAULT_OWNER = 'anchorium-vault-film-1430.testnet'
SEGMENTS = [
    # 0:00 hook: the hook card (frame 1 = the red verdict, filled from the real tx), then the real tx
    Seg('s0_hook', 'card', 'v_hook.mp4', 5.5),
    Seg('s1b_overruled', 'still', 'gallery/04_overruled.png', 3.2, fit='still32', dot='bad',
        label='NEAR testnet RPC · real tx 4KxLnhn9… · 10 Nov 2026', zoom=(1.0, 1.2, (900, 860)),
        caps=[(0, 3.2, 'The real transaction: rejected, and flagged with a verdict_override event.')]),
    # 0:08 title + 3 problems, 0:18 how it works
    Seg('s2_title', 'card', 'v_title.mp4', 10.0),
    Seg('s3_arch', 'card', 'v_arch.mp4', 12.0),
    # 0:30 a full cycle on-chain (fresh media guard, filmed 12 Nov)
    Seg('c_cycle', 'card', 'ch_cycle.mp4', 1.8),
    Seg('s4a_cycle', 'clip', '04_cycle.mp4', 12.0, start=2.2, speed=2.0, badge='⏩ 2×', dot='blue',
        label=f'make demo-onchain · fresh guard {MEDIA_GUARD} · 12 Nov',
        caps=[(0, 6, 'A brand-new guard contract. The agent must ask it before every trade.'),
              (6, 12, 'A real NEAR Intents 1Click quote; every decision is a real testnet transaction.')]),
    Seg('s4b_cycle_hold', 'still', '04_cycle_end.png', 6.0, dot='blue', label=f'{MEDIA_GUARD} · real txs, DRY RUN',
        boxes=[(178, 214, 290, 34, 'ok', 0.3), (178, 568, 740, 34, 'warn', 1.6), (178, 744, 1410, 34, 'bad', 2.9)],
        zoom=(1.0, 1.1, (880, 500)),
        caps=[(0, 6, 'Allowed and journaled; the contract computes the paper result itself. The cheat: rejected.')]),
    # 0:50 NEW: the Vault (fresh testnet deployment, filmed 13 Nov)
    Seg('c_vault', 'card', 'ch_vault.mp4', 1.8),
    Seg('s6a_vault', 'clip', '06_vault.mp4', 11.8, start=0.0, speed=8.0, badge='⏩ 8×', dot='ok',
        label=f'vault-demo · vault.{VAULT_OWNER} · real testnet txs',
        caps=[(0, 6, 'The Vault holds the money. The agent can only ask it to pay for one journaled decision.'),
              (6, 11.8, 'The vault reads that decision from the guard on-chain before it pays anything.')]),
    Seg('s6b_vault_hold', 'still', '06_vault_end.png', 12.0, dot='ok',
        label=f'vault.{VAULT_OWNER} · TEST-ONLY mock token · 13 Nov',
        boxes=[(515, 158, 300, 32, 'bad', 0.2), (525, 364, 122, 32, 'ok', 3.0), (1074, 511, 236, 32, 'bad', 6.0),
               (1074, 746, 182, 32, 'bad', 8.5)],
        zoom=(1.0, 1.06, (960, 500)),
        caps=[(0, 3, 'Rejected decision: the vault pays nothing.'),
              (3, 6, 'Allowed $2.00 decision: it pays exactly 2 test USDC to the journaled deposit address.'),
              (6, 8.5, 'The same decision again: refused.'),
              (8.5, 12, 'Paused by the owner: even an allowed decision is refused. The owner can still withdraw.')]),
    # 1:15 NEW: NEAR AI Cloud
    Seg('c_ai', 'card', 'ch_ai.mp4', 1.8),
    Seg('s8a_ai', 'clip', '08_near_ai.mp4', 8.0, start=0.0, speed=2.0, badge='⏩ 2×', dot='blue',
        label='make verify-ai --full · NEAR AI Cloud attestation (13 Nov) + NVIDIA NRAS',
        caps=[(0, 8, 'The AI risk co-pilot runs only on NEAR AI Cloud, inside a TEE. It can veto, never approve.')]),
    Seg('s8b_ai_hold', 'still', '08_near_ai_end.png', 11.0, dot='blue', label='verify-ai · zero keys, zero cost',
        boxes=[(40, 216, 1580, 90, 'ok', 0.3), (40, 423, 270, 30, 'blue', 6.5)], zoom=(1.0, 1.12, (820, 300)),
        caps=[(0, 6.5, 'Offline check: each TEE key is bound to our fresh nonce in Intel TDX quotes; NVIDIA confirms the GPU.'),
              (6.5, 11, 'Each review is signed in the TEE and hashed on-chain. Signed reviews wait for NEAR AI credits.')]),
    # 1:35 safety (kept, trimmed)
    Seg('s5a_keys_hold', 'still', '05a_keys_end.png', 4.0, dot='ok', label='agent.anchorium-team.testnet · 1 key',
        boxes=[(434, 510, 340, 112, 'ok', 0), (434, 628, 660, 32, 'ok', 0)], zoom=(1.1, 1.3, (760, 580)),
        caps=[(0, 4, "The bot's only key can journal decisions. It can't move funds, pause, or change the rules.")]),
    Seg('s5b_pause_hold', 'still', '05b_pause_end.png', 4.0, dot='bad', label=f'{MEDIA_GUARD} · pause, then resume',
        boxes=[(108, 158, 870, 32, 'bad', 0), (64, 452, 280, 60, 'bad', 0.4), (64, 893, 250, 60, 'ok', 1.2)],
        zoom=(1.0, 1.08, (700, 540)),
        caps=[(0, 4, 'One owner transaction pauses the guard: every new trade stops.')]),
    # 1:43 optional fiat on-ramp
    Seg('s9_fiat', 'still', '09_fiat_end.png', 7.0, dot='warn',
        label='optional fiat on-ramp · PayPal sandbox · offline simulation (no keys)',
        boxes=[(40, 276, 1840, 60, 'warn', 0.3), (40, 335, 600, 30, 'ok', 1.2)], zoom=(1.0, 1.1, (900, 260)),
        caps=[(0, 7, 'Fiat in. NEAR enforces.')]),
    # 1:50 verify it yourself (fresh clone, filmed 13 Nov)
    Seg('s7b_time', 'still', '07_verify_demo.png', 3.0, dot='ok', label='fresh git clone · time (make setup && make demo)',
        boxes=[(40, 920, 270, 94, 'ok', 0)], zoom=(1.0, 1.0, None), caps=[(0, 3, "Don't trust us either: clone it and run it. No keys, no funds.")]),
    Seg('s7d_tests_hold', 'still', '07_verify_end.png', 4.0, dot='ok', label='fresh clone · test results',
        boxes=[(36, 66, 1350, 154, 'ok', 0)], zoom=(1.25, 1.35, (700, 160)), caps=[(0, 4, '454 Python tests, 40 + 2 guard and 16 + 1 vault contract tests pass.')]),
    # end card
    Seg('s8_end', 'card', 'v_end.mp4', 8.5),
]
CUT_60 = ['s0_hook', 's1b_overruled', 's3_arch', 's6a_vault', 's6b_vault_hold', 's8_end']
XFADE = 0.3  # cross-fade between every two segments


def probe(path: Path) -> float:
    return float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0',
                                 str(path)], capture_output=True, text=True).stdout)


def concat(names: list[str], out: Path) -> tuple[float, list[float]]:
    """Joins the segments with XFADE-second cross-fades; returns the duration and each segment's start time."""
    files = [SEG / f'{n}.mp4' for n in names]
    lens = [probe(f) for f in files]
    starts, chain, last, acc = [0.0], [], '0:v', lens[0]
    for k in range(1, len(files)):
        off = acc - XFADE
        starts.append(off)
        chain.append(f'[{last}][{k}:v]xfade=transition=fade:duration={XFADE}:offset={off:.3f}[x{k}]')
        last, acc = f'x{k}', off + lens[k]
    args = [a for f in files for a in ('-i', str(f))]
    ff(*args, '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-filter_complex', ';'.join(chain) + f';[{last}]format=yuv420p[v]',
       '-map', '[v]', '-map', f'{len(files)}:a', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-r', str(FPS),
       '-c:a', 'aac', '-b:a', '192k', '-shortest', '-movflags', '+faststart', str(out))
    return probe(out), starts


def srt(segs: list[Seg], starts: list[float], out: Path) -> None:
    def ts(t: float) -> str:
        ms = int(round(t * 1000))
        return f'{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}'
    lines, n = [], 1
    for s, t in zip(segs, starts):
        for t0, t1, text in s.caps:
            lines.append(f'{n}\n{ts(t + t0)} --> {ts(t + t1)}\n{text}\n')
            n += 1
    out.write_text('\n'.join(lines))


if __name__ == '__main__':
    for d in (SEG, OVL, OUT, BUILD):
        d.mkdir(parents=True, exist_ok=True)
    for f in ('gallery.css', 'video.css', 'v_overlay.html'):
        shutil.copy(MEDIA / 'templates' / f, BUILD / f)
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={'width': 1920, 'height': 1080})
        for s in SEGMENTS:
            build_segment(s, page)
            print(f'segment {s.name}: {s.dur:.2f} s', flush=True)
        b.close()
    full, starts = concat([s.name for s in SEGMENTS], OUT / 'anchorium_demo_full.mp4')
    short, _ = concat(CUT_60, OUT / 'anchorium_demo_60s.mp4')
    srt(SEGMENTS, starts, OUT / 'captions.srt')
    titles = {'s0_hook': 'The bot gets caught', 's2_title': 'The problem', 's3_arch': 'How it works',
              'c_cycle': 'A full cycle on-chain', 'c_vault': 'The Vault holds the money',
              'c_ai': 'NEAR AI Cloud: AI reviews you can verify', 's5a_keys_hold': 'Safety and the fiat on-ramp',
              's7b_time': 'Verify it yourself'}
    (OUT / 'chapters.txt').write_text(''.join(f'{int(t // 60)}:{int(t % 60):02d} {titles[s.name]}\n'
                                              for s, t in zip(SEGMENTS, starts) if s.name in titles))
    print(f'full: {full:.2f} s · 60s cut: {short:.2f} s')
    assert full <= 135 and short <= 60, 'length limit exceeded'
