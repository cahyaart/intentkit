"""Neon Tech" remake of the KTP-el KAMU HILANG? motion infographic.

Same rule as render.py: the poster's own pixels are the only source of text,
logos, characters and icons. This version stages the poster as a 3D card in a
dark navy scene (light beams, bokeh particles, neon grid floor), with a neon
rim, whip-pan transitions with real motion blur, a spotlight on the active
step and neon ripples around the step badges.

Usage:
    python render_neon.py [output.mp4] [--preview]
"""

import math
import os
import subprocess
import sys

import cv2
import imageio_ffmpeg
import numpy as np

import render as R
from render import (DUR, FPS, H, PH, PW, SRC, SYNC, TAU, W, XX, YY, bump, clamp01,
                    damped, ease, interp_keys, ramp)

HERE = R.HERE

# re-time the shared poster animations for this edit
R.HEADER_T0 = 0.95
R.BUB_HIDE, R.BUB_POP = 9.1, 10.25

CYAN = np.array([1.0, 0.86, 0.30], np.float32)      # BGR
ICE = np.array([1.0, 0.95, 0.80], np.float32)
VIOLET = np.array([0.95, 0.45, 0.35], np.float32)
NAVY_DK = np.array([0.12, 0.05, 0.02], np.float32)

# --------------------------------------------------------------------------
# Camera: (cx, cy, zoom, rotY deg, rotX deg) around the poster point (cx, cy)
# --------------------------------------------------------------------------
FULL = (540.0, 675.0, 0.93, 0.0, 0.0)
CAM_KEYS = [
    (0.0, (540.0, 675.0, 0.60, -52.0, 12.0)),
    (0.35, (540.0, 675.0, 0.60, -52.0, 12.0)),
    (1.55, (540.0, 675.0, 0.93, -7.0, 3.0)),      # card lands
    (2.9, (345.0, 600.0, 1.60, 5.0, 1.0)),        # push-in on title
    (3.35, (345.0, 600.0, 1.63, 6.0, 1.0)),
    (3.8, (360.0, 625.0, 1.50, -5.0, 0.0)),       # whip -> step 1
    (5.85, (640.0, 625.0, 1.50, 4.0, 0.0)),
    (6.3, (360.0, 745.0, 1.50, -5.0, 0.0)),       # whip -> step 2
    (8.45, (715.0, 745.0, 1.50, 4.0, 0.0)),
    (8.65, (718.0, 745.0, 1.51, 4.0, 0.0)),
    (9.1, (360.0, 830.0, 1.50, -5.0, 0.0)),       # whip -> step 3
    (10.8, (715.0, 830.0, 1.50, 4.0, 0.0)),
    (11.5, (718.0, 830.0, 1.53, 5.0, 0.0)),
    (12.6, (540.0, 675.0, 0.93, -6.0, 2.0)),      # pull back
    (13.4, FULL),
    (15.0, (540.0, 675.0, 0.955, 2.0, -1.0)),
]
WHIPS = [(3.35, 3.8), (5.85, 6.3), (8.65, 9.1), (11.5, 12.6)]
FLARES = [0.3, 1.1, 3.57, 6.07, 8.87, 12.05]
STEP_IN = {1: 3.8, 2: 6.3, 3: 9.1}
BADGES = {1: (42, 513, 45), 2: (48, 810, 47), 3: (46, 1071, 46)}

FOCUS_KEYS = [
    (0.0, (1, 0, 0, 0)),
    (3.45, (1, 0, 0, 0)),
    (3.75, (0, 1, 0, 0)),
    (5.95, (0, 1, 0, 0)),
    (6.25, (0, 0, 1, 0)),
    (8.75, (0, 0, 1, 0)),
    (9.05, (0, 0, 0, 1)),
    (15.0, (0, 0, 0, 1)),
]


def spot_strength(t):
    return ramp(t, 1.6, 2.6) * (1 - ramp(t, 11.5, 12.4))


# --------------------------------------------------------------------------
# Poster-space extras: spotlight, badge ripples, title shine
# --------------------------------------------------------------------------
ROWS = [R.row_mask(-100, 458), R.row_mask(428, 756), R.row_mask(756, 1016),
        R.row_mask(1004, 1258)]


def spotlight(img, t):
    s = spot_strength(t)
    if s <= 0.001:
        return img
    wts = interp_keys(t, FOCUS_KEYS)
    M = np.zeros((PH, PW), np.float32)
    for w, r in zip(wts, ROWS):
        if w > 0:
            M = np.maximum(M, r * w)
    bw = (s * 0.88 * (1 - M))[..., None]
    dark = cv2.GaussianBlur(img, (0, 0), 2.6) * 0.34 + NAVY_DK * 0.66
    return img * (1 - bw) + dark * bw


def add_glow(img, x0, y0, layer, color, sigma):
    """Additive glow: layer is a small float mask placed at (x0, y0)."""
    g = cv2.GaussianBlur(layer, (0, 0), sigma) * 1.6 + layer
    h, w = g.shape
    xa, ya = max(0, x0), max(0, y0)
    xb, yb = min(PW, x0 + w), min(PH, y0 + h)
    if xa >= xb or ya >= yb:
        return
    g = g[ya - y0:yb - y0, xa - x0:xb - x0]
    roi = img[ya:yb, xa:xb]
    roi[:] = 1 - (1 - roi) * (1 - np.clip(g, 0, 1)[..., None] * color)


def badge_fx(img, t):
    S = 4
    for step, t0 in STEP_IN.items():
        cx, cy, r = BADGES[step]
        glow_a = ramp(t, t0 - 0.1, t0 + 0.3) * (1 - ramp(t, t0 + 2.3, t0 + 2.7))
        glow_a *= 0.75 + 0.25 * math.sin(TAU * (t - t0) / 1.1)
        glow_a = max(glow_a, bump(t, SYNC, 1.0) * 0.8)
        rings = []
        for k in range(2):
            u = (t - t0 - 0.05 - 0.4 * k) / 0.9
            if 0 < u < 1:
                rings.append((r + 3 + 34 * ease(u), (1 - u) ** 1.5))
        if glow_a <= 0.01 and not rings:
            continue
        size = 2 * (r + 60)
        x0, y0 = int(cx - size / 2), int(cy - size / 2)
        big = np.zeros((size * S, size * S), np.uint8)
        c = (int((cx - x0) * S), int((cy - y0) * S))
        layer = np.zeros((size, size), np.float32)
        if glow_a > 0.01:
            cv2.circle(big, c, int((r + 2) * S), 255, int(2.2 * S), cv2.LINE_AA)
            layer += cv2.resize(big, (size, size), interpolation=cv2.INTER_AREA) / 255.0 * glow_a
        for rad, a in rings:
            big[:] = 0
            cv2.circle(big, c, int(rad * S), 255, int(2.4 * S), cv2.LINE_AA)
            layer += cv2.resize(big, (size, size), interpolation=cv2.INTER_AREA) / 255.0 * a
        add_glow(img, x0, y0, np.clip(layer, 0, 1), CYAN, 5)


_tb = SRC[165:384, 15:612]
M_TITLE = np.zeros((PH, PW), np.float32)
M_TITLE[165:384, 15:612] = ((_tb[..., 0] - _tb[..., 2] > 0.15) & (_tb.mean(axis=2) < 0.45))
M_TITLE = cv2.GaussianBlur(M_TITLE, (0, 0), 0.7)


def poster_frame(t):
    img, dx, dy = R.warp(SRC, t)
    R.draw_header(img, t)
    R.draw_bubble(img, t)
    for face, eyes in R.EYES.items():
        p = R.blink_amount(t, R.BLINKS[face])
        if p <= 0.01:
            continue
        for (ex, ey, ew, eh), skin in zip(eyes, R.EYE_SKIN[face]):
            ix, iy = int(round(ex)), int(round(ey))
            R.draw_blink(img, ex + dx[iy, ix], ey + dy[iy, ix], ew, eh, p, skin)
    R.highlights(img, t)
    R.sweep(img, M_TITLE, t, 2.55, dur=0.8, strength=0.55, width=34)
    R.sweep(img, M_TITLE, t, SYNC + 0.1, dur=0.9, strength=0.45, width=34)
    img = spotlight(img, t)
    badge_fx(img, t)
    return np.clip(img, 0, 1)


# --------------------------------------------------------------------------
# Scene background: gradient, light beams, bokeh, neon floor
# --------------------------------------------------------------------------
_gy, _gx = np.mgrid[0:H, 0:W].astype(np.float32)
_top = np.array([0.30, 0.12, 0.04], np.float32)
_bot = np.array([0.12, 0.04, 0.015], np.float32)
BASE = _top + (_bot - _top) * (_gy / H)[..., None]
_rad = np.exp(-(((_gx - 540) / 700) ** 2 + ((_gy - 820) / 900) ** 2))
BASE = BASE + np.array([0.75, 0.32, 0.10], np.float32) * (0.55 * _rad)[..., None]
_vig = np.clip(1 - 0.55 * (((_gx - 540) / 760) ** 2 + ((_gy - 960) / 1250) ** 2), 0.35, 1)
BASE *= _vig[..., None]

BEAM_O = (540.0, -260.0)


def _beams():
    ang = np.arctan2(_gx - BEAM_O[0], _gy - BEAM_O[1]) * 180 / math.pi
    dist = np.hypot(_gx - BEAM_O[0], _gy - BEAM_O[1])
    b = np.zeros((H, W), np.float32)
    for a0, wdt, k in [(-28, 4.5, 0.9), (-14, 2.5, 0.6), (-3, 6.0, 1.0),
                       (9, 3.0, 0.7), (21, 5.0, 0.85), (33, 2.2, 0.5)]:
        b += k * np.exp(-((ang - a0) / wdt) ** 2)
    b *= np.clip(1.25 - dist / 2100, 0, 1)
    return b


BEAMS = _beams()

rng = np.random.default_rng(7)
N_P = 70
P_X = rng.uniform(-100, W + 100, N_P)
P_Y = rng.uniform(0, H, N_P)
P_D = rng.uniform(0.25, 1.0, N_P)
P_R = 2 + 9 * P_D ** 2
P_V = 25 + 50 * P_D
P_PH = rng.uniform(0, TAU, N_P)
P_B = rng.uniform(0.25, 0.7, N_P)
P_C = rng.integers(0, 3, N_P)
P_COLS = [CYAN, ICE, VIOLET]


def _sprite(r):
    s = int(r * 3) * 2 + 1
    y, x = np.mgrid[0:s, 0:s].astype(np.float32) - s // 2
    return np.exp(-(x * x + y * y) / (2 * (r * 0.6) ** 2))


def particles(frame, t, cx, cy):
    for i in range(N_P):
        x = P_X[i] + 18 * math.sin(t * 0.6 + P_PH[i]) - (cx - 540) * 0.35 * P_D[i]
        y = (P_Y[i] - P_V[i] * t - (cy - 675) * 0.25 * P_D[i]) % (H + 200) - 100
        a = P_B[i] * (0.6 + 0.4 * math.sin(t * 1.7 + P_PH[i] * 3))
        spr = _sprite(P_R[i])
        s = spr.shape[0]
        x0, y0 = int(x) - s // 2, int(y) - s // 2
        xa, ya, xb, yb = max(0, x0), max(0, y0), min(W, x0 + s), min(H, y0 + s)
        if xa >= xb or ya >= yb:
            continue
        g = spr[ya - y0:yb - y0, xa - x0:xb - x0, None] * a * P_COLS[P_C[i]]
        frame[ya:yb, xa:xb] += g


HORIZON = 1430.0


def floor_grid(t, cx):
    layer = np.zeros((H, W), np.float32)
    S = 2
    big = np.zeros((int((H - HORIZON) * S), W * S), np.uint8)
    vpx = 540 - (cx - 540) * 0.2
    for k in range(-14, 15):
        xb = vpx + k * 150
        cv2.line(big, (int(vpx * S), 0), (int((vpx + (xb - vpx) * 6) * S), big.shape[0]),
                 255, S, cv2.LINE_AA)
    scroll = (t * 0.55) % 1.0
    for j in range(14):
        zz = (j + 1 - scroll)
        y = 1300.0 / (zz + 0.4)
        if y > H - HORIZON:
            continue
        cv2.line(big, (0, int(y * S)), (W * S, int(y * S)), 255, S, cv2.LINE_AA)
    sm = cv2.resize(big, (W, int(H - HORIZON)), interpolation=cv2.INTER_AREA) / 255.0
    fade = np.clip((np.arange(sm.shape[0], dtype=np.float32) / sm.shape[0]) * 1.6, 0, 1) ** 1.2
    layer[int(HORIZON):] = sm * fade[:, None]
    glow = cv2.GaussianBlur(layer, (0, 0), 4)
    return layer * 0.45 + glow * 0.9


def background(t, cx, cy, z, ay):
    fade = ramp(t, 0.0, 0.45)
    frame = BASE.copy()
    rot = 4 * math.sin(TAU * t / 11.0) - ay * 0.3
    Mr = cv2.getRotationMatrix2D(BEAM_O, rot, 1.0)
    Mr[0, 2] -= (cx - 540) * 0.12
    beams = cv2.warpAffine(BEAMS, Mr, (W, H), flags=cv2.INTER_LINEAR)
    frame += (beams * (0.10 + 0.03 * math.sin(TAU * t / 3.3)))[..., None] * CYAN
    frame += floor_grid(t, cx)[..., None] * (CYAN * 0.55)
    particles(frame, t, cx, cy)
    return frame * fade


# --------------------------------------------------------------------------
# 3D card
# --------------------------------------------------------------------------
PAD = 90
CARD_A = R.rounded_alpha(PW, PH, 22)
_pa = np.zeros((PH + 2 * PAD, PW + 2 * PAD), np.float32)
_pa[PAD:PAD + PH, PAD:PAD + PW] = CARD_A
_edge = np.clip(cv2.dilate(_pa, np.ones((5, 5), np.uint8)) - _pa, 0, 1)
_outer = cv2.GaussianBlur(_pa, (0, 0), 22)
_outer = np.clip(_outer / _outer.max() * (1 - _pa) * 1.3, 0, 1)
_py, _px = np.mgrid[0:PH + 2 * PAD, 0:PW + 2 * PAD].astype(np.float32)
_mix = np.clip((_px / (PW + 2 * PAD)) * 0.5 + (_py / (PH + 2 * PAD)) * 0.5, 0, 1)[..., None]
_rimcol = CYAN * (1 - _mix) + VIOLET * _mix
RIM = (_edge * 0.9 + _outer * 0.55)[..., None] * _rimcol
DFOC = 2600.0


def homography(cx, cy, z, ay, ax, pad=0):
    a, b = math.radians(ay), math.radians(ax)
    cay, say, cax, sax = math.cos(a), math.sin(a), math.cos(b), math.sin(b)
    src, dst = [], []
    for (x, y) in [(0, 0), (PW, 0), (PW, PH), (0, PH)]:
        X, Y, Z = x - cx, y - cy, 0.0
        X, Z = X * cay + Z * say, -X * say + Z * cay
        Y, Z = Y * cax - Z * sax, Y * sax + Z * cax
        Z += DFOC
        dst.append((W / 2 + z * DFOC * X / Z, H / 2 + z * DFOC * Y / Z))
        src.append((x + pad, y + pad))
    return cv2.getPerspectiveTransform(np.float32(src), np.float32(dst))


def rim_strength(t):
    base = 0.55 + 0.45 * (1 - ramp(t, 2.0, 3.0)) + 0.45 * ramp(t, 12.0, 12.8)
    return base * ramp(t, 0.35, 1.2) * (1 + 0.5 * bump(t, SYNC, 1.0)
                                        + 0.15 * math.sin(TAU * t / 2.2))


def compose(poster, t, cam=None):
    cx, cy, z, ay, ax = cam if cam is not None else interp_keys(t, CAM_KEYS)
    z *= 1 + 0.012 * bump(t, SYNC, 1.0)
    frame = background(t, cx, cy, z, ay)
    Hr = homography(cx, cy, z, ay, ax, PAD)
    rim = cv2.warpPerspective(RIM, Hr, (W, H), flags=cv2.INTER_LINEAR)
    frame += rim * rim_strength(t)
    Hp = homography(cx, cy, z, ay, ax)
    p = cv2.warpPerspective(poster, Hp, (W, H), flags=cv2.INTER_LANCZOS4)
    a = cv2.warpPerspective(CARD_A, Hp, (W, H), flags=cv2.INTER_LINEAR)
    a = (a * ramp(t, 0.35, 1.0))[..., None]
    frame = frame * (1 - a) + p * a
    if z > 1.2:
        k = min(1.0, (z - 1.2) / 0.3) * 0.35
        soft = cv2.GaussianBlur(frame, (0, 0), 1.1)
        frame = frame + (frame - soft) * k
    return frame


def _hud_layer():
    S = 2
    big = np.zeros((H * S, W * S), np.uint8)
    m, L = 44, 86
    for (x, y, sx, sy) in [(m, m, 1, 1), (W - m, m, -1, 1), (m, H - m, 1, -1),
                           (W - m, H - m, -1, -1)]:
        pts = np.array([(x, y + sy * L), (x, y), (x + sx * L, y)], np.float32) * S
        cv2.polylines(big, [pts.astype(np.int32)], False, 255, 3 * S, cv2.LINE_AA)
    hud = cv2.resize(big, (W, H), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    return np.clip(hud + cv2.GaussianBlur(hud, (0, 0), 6) * 1.5, 0, 1)


HUD = _hud_layer()
EDGE_VIG = np.clip(((_gx - 540) / 620) ** 2 + ((_gy - 960) / 1100) ** 2, 0, 1) ** 1.5


def overlays(frame, t, z):
    # cinematic edge falloff while the camera is close to the card
    v = clamp01((z - 1.0) / 0.4) * 0.45
    if v > 0:
        frame *= (1 - v * EDGE_VIG)[..., None]
    a = ramp(t, 1.6, 2.3) * (0.8 + 0.2 * math.sin(TAU * t / 1.7))
    a *= 1 - 0.6 * ramp(t, 12.4, 13.0)
    if a > 0:
        frame[:] = 1 - (1 - frame) * (1 - (HUD * a)[..., None] * CYAN)


def flare(frame, t):
    for t0 in FLARES:
        u = (t - t0) / 0.5
        if not 0 < u < 1:
            continue
        a = math.sin(math.pi * u) ** 2
        xc = -300 + (W + 600) * u
        yc = 960.0
        band = np.exp(-((_gy[800:1120] - yc) / 5.0) ** 2) * np.exp(-((_gx[800:1120] - xc) / 520) ** 2)
        halo = np.exp(-((_gy[800:1120] - yc) / 60.0) ** 2) * np.exp(-((_gx[800:1120] - xc) / 260) ** 2)
        g = (band * 0.8 + halo * 0.25) * a
        frame[800:1120] = 1 - (1 - frame[800:1120]) * (1 - g[..., None] * ICE)


def in_whip(t):
    return any(t0 - 0.02 <= t <= t1 + 0.02 for t0, t1 in WHIPS)


def render_frame(t):
    poster = poster_frame(t)
    if in_whip(t):
        n = 7
        acc = np.zeros((H, W, 3), np.float32)
        for k in range(n):
            ts = t + (k / (n - 1) - 0.5) / FPS
            acc += compose(poster, t, interp_keys(ts, CAM_KEYS))
        frame = acc / n
    else:
        frame = compose(poster, t)
    overlays(frame, t, interp_keys(t, CAM_KEYS)[2])
    flare(frame, t)
    return (np.clip(frame, 0, 1) * 255 + 0.5).astype(np.uint8)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = args[0] if args else os.path.join(HERE, "ktp-el-hilang-9x16-neon.mp4")
    if "--preview" in sys.argv:
        pdir = os.path.join(os.path.dirname(os.path.abspath(out)), "preview_neon")
        os.makedirs(pdir, exist_ok=True)
        for t in [0.3, 0.8, 1.4, 2.6, 3.55, 4.4, 5.6, 7.2, 8.2, 9.6, 10.6, 11.3, 12.1, 13.0, 14.5]:
            cv2.imwrite(os.path.join(pdir, f"t{t:05.2f}.png"), render_frame(t))
        return
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [ffmpeg, "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
           "-map", "0:v", "-map", "1:a", "-shortest",
           "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p",
           "-profile:v", "high", "-level", "4.2", "-colorspace", "bt709",
           "-color_primaries", "bt709", "-color_trc", "bt709",
           "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    n = int(round(DUR * FPS))
    for i in range(n):
        proc.stdin.write(render_frame(i / FPS).tobytes())
        if i % 30 == 0:
            print(f"frame {i}/{n}", flush=True)
    proc.stdin.close()
    proc.wait()
    print("wrote", out)


if __name__ == "__main__":
    main()
