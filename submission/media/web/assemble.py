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
SEGMENTS = [
    # 1 cold open (starts on the red verdict: YouTube may use frame 1 as the thumbnail)
    Seg('s1a_cold', 'still', '01_cold_open_end.png', 4.5, label='make demo · demo.anchorium-team.testnet (keyless)',
        dot='bad', boxes=[(770, 624, 280, 38, 'bad', 0)], zoom=(1.5, 1.8, (700, 645)),
        caps=[(0, 4.5, 'A simulated compromised agent asks for $4.99, 2.5x its $2.00 cap, and claims it is allowed.')]),
    Seg('s1b_overruled', 'still', 'gallery/04_overruled.png', 6.5, fit='still32', dot='bad',
        label='NEAR testnet RPC · real tx 4KxLnhn9… · 10 Nov 2026', zoom=(1.0, 1.25, (900, 860)),
        caps=[(0, 6.5, 'The NEAR contract says no, and journals it on-chain with a verdict_override event.')]),
    # 2 problem, 3 how it works
    Seg('s2_title', 'card', 'v_title.mp4', 15.0),
    Seg('s3_arch', 'card', 'v_arch.mp4', 20.0),
    # 4 a full cycle on-chain (fresh media guard)
    Seg('s4a_cycle', 'clip', '04_cycle.mp4', 16.4, start=2.2, speed=2.0, badge='⏩ 2×', dot='blue',
        label=f'make demo-onchain · fresh guard {MEDIA_GUARD}',
        caps=[(0, 6, 'A brand-new guard deployed for this video. The agent must ask it before every trade.'),
              (6, 11.5, 'A real NEAR Intents 1Click quote: worst-case slippage has to fit the on-chain policy.'),
              (11.5, 16.4, 'Every decision is a real testnet transaction, journaled by the contract.')]),
    Seg('s4b_cycle_hold', 'still', '04_cycle_end.png', 6.0, dot='blue', label=f'{MEDIA_GUARD} · real txs, DRY RUN',
        boxes=[(178, 214, 290, 34, 'ok', 0.3), (178, 568, 740, 34, 'warn', 1.6), (178, 744, 1410, 34, 'bad', 2.9)], zoom=(1.0, 1.1, (880, 500)),
        caps=[(0, 6, 'Allowed and journaled. The contract computes the paper result itself (here −$0.02). The cheat: rejected.')]),
    Seg('s4c_dashboard', 'clip', 'clips/dashboard.mp4', 14.0, start=0.5, dot='ok',
        label='make dashboard · reads guard.anchorium-team.testnet',
        caps=[(0, 7, 'The dashboard reads every decision straight from the contract.'),
              (7, 14, 'Signal, guard verdict, quote, where the funds are, and the outcome.')]),
    # 5 safety
    Seg('s5a_keys', 'clip', '05a_keys.mp4', 6.5, start=0.0, speed=2.0, badge='⏩ 2×', dot='ok',
        label='keyless RPC · view_access_key_list agent.anchorium-team.testnet',
        caps=[(0, 6.5, "The bot's only key: a function-call key for one contract and two journal methods.")]),
    Seg('s5a_keys_hold', 'still', '05a_keys_end.png', 3.0, dot='ok', label='agent.anchorium-team.testnet · 1 key',
        boxes=[(434, 510, 340, 112, 'ok', 0), (434, 628, 660, 32, 'ok', 0)], zoom=(1.1, 1.35, (760, 580)),
        caps=[(0, 3, "It can't move funds, pause, or change the rules.")]),
    Seg('s5b_pause', 'clip', '05b_pause.mp4', 8.2, start=0.0, speed=2.0, badge='⏩ 2×', dot='bad',
        label=f'owner kill switch · {MEDIA_GUARD}',
        caps=[(0, 8.2, 'The owner sends one pause transaction to the guard.')]),
    Seg('s5b_pause_hold', 'still', '05b_pause_end.png', 3.5, dot='bad', label=f'{MEDIA_GUARD} · pause, then resume',
        boxes=[(108, 158, 870, 32, 'bad', 0), (64, 452, 280, 60, 'bad', 0.4), (64, 893, 250, 60, 'ok', 1.2)], zoom=(1.0, 1.08, (700, 540)),
        caps=[(0, 3.5, 'The next check returns PAUSED. Resume, and it is OK again.')]),
    Seg('s5c_auto', 'clip', '05c_autopause.mp4', 10.0, start=0.5, speed=2.0, badge='⏩ 2×', dot='ok',
        label='local sandbox test · contracts/anchorium-guard',
        caps=[(0, 10, 'At the $1 daily loss cap, the contract pauses itself.')]),
    Seg('s5c_auto_hold', 'still', '05c_autopause_end.png', 3.0, dot='ok', label='local sandbox test · local NEAR node',
        boxes=[(92, 100, 1110, 292, 'blue', 0), (40, 508, 1350, 36, 'ok', 0.3)], zoom=(1.1, 1.25, (720, 320)),
        caps=[(0, 3, 'Proven by a sandbox test on a local NEAR node.')]),
    # 6 honest money
    Seg('s6_money', 'still', 'gallery/09_honest_money.png', 14.0, fit='still32', dot='warn',
        label='PAPER · real 1Click quotes, simulated fill', zoom=(1.0, 1.12, (900, 640)),
        caps=[(0, 7, 'Every fee, the gas and a 31.2% tax reserve are counted before anything is called a profit.'),
              (7, 14, 'Wins and losses are both shown. These are paper trades, and they are labelled paper.')]),
    # 7 verify it yourself (fresh clone)
    Seg('s7a_clone', 'clip', '07_verify.mp4', 13.75, start=0.0, speed=4.0, badge='⏩ 4×', dot='ok',
        label='fresh git clone from GitHub · no keys, no funds',
        caps=[(0, 13.75, "Don't trust us either: clone it and run two commands. No keys, no funds.")]),
    Seg('s7b_time', 'still', '07_verify_demo.png', 2.5, dot='ok', label='time (make setup && make demo)',
        boxes=[(40, 920, 270, 94, 'ok', 0)], zoom=(1.6, 1.9, (420, 900)), caps=[(0, 2.5, 'Setup + demo: 30 seconds on this machine.')]),
    Seg('s7c_tests', 'clip', '07_verify.mp4', 6.6, start=160.9, speed=4.0, badge='⏩ 4×', dot='ok',
        label='make test-py · make contract-test', caps=[(0, 6.6, 'Then the full test suites.')]),
    Seg('s7d_tests_hold', 'still', '07_verify_end.png', 2.5, dot='ok', label='fresh clone · test results',
        boxes=[(40, 98, 1350, 92, 'ok', 0)], zoom=(1.25, 1.4, (700, 200)), caps=[(0, 2.5, '388 Python tests and 40 + 2 contract tests pass.')]),
    # 8 end card
    Seg('s8_end', 'card', 'v_end.mp4', 10.0),
]
CUT_60 = ['s1a_cold', 's1b_overruled', 's3_arch_12', 's4a_cycle', 's4b_cycle_hold', 's8_end']


def concat(names: list[str], out: Path) -> float:
    lst = SEG / f'{out.stem}.txt'
    lst.write_text(''.join(f"file '{SEG / n}.mp4'\n" for n in names))
    ff('-f', 'concat', '-safe', '0', '-i', str(lst), '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
       '-map', '0:v', '-map', '1:a', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p',
       '-r', str(FPS), '-c:a', 'aac', '-b:a', '192k', '-shortest', '-movflags', '+faststart', str(out))
    return float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(out)],
                                capture_output=True, text=True).stdout)


def srt(segs: list[Seg], out: Path) -> None:
    def ts(t: float) -> str:
        ms = int(round(t * 1000))
        return f'{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}'
    lines, t, n = [], 0.0, 1
    for s in segs:
        for t0, t1, text in s.caps:
            lines.append(f'{n}\n{ts(t + t0)} --> {ts(t + t1)}\n{text}\n')
            n += 1
        t += s.dur
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
    ff('-i', str(SEG / 's3_arch.mp4'), '-t', '12', '-c', 'copy', str(SEG / 's3_arch_12.mp4'))
    full = concat([s.name for s in SEGMENTS], OUT / 'anchorium_demo_full.mp4')
    short = concat(CUT_60, OUT / 'anchorium_demo_60s.mp4')
    srt(SEGMENTS, OUT / 'captions.srt')
    print(f'full: {full:.2f} s · 60s cut: {short:.2f} s')
    assert full <= 180 and short <= 60, 'length limit exceeded'
