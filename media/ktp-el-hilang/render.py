"""Render the "KTP-el KAMU HILANG?" poster as a 9:16 motion-infographic video.

Every pixel of text, logo, character and icon comes from the master poster
(poster.jpg). Motion is produced only with camera moves, small rigid/soft
warps, eyelid overlays for blinking, and light/glow passes, so the text is
never redrawn or retyped.

Usage:
    python render.py [output.mp4] [--preview]

Requires: numpy, opencv-python-headless, imageio-ffmpeg.
"""

import math
import os
import subprocess
import sys

import cv2
import imageio_ffmpeg
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 1080, 1920
FPS = 30
DUR = 15.0

SRC = cv2.imread(os.path.join(HERE, "poster.jpg")).astype(np.float32) / 255.0
PH, PW = SRC.shape[:2]  # 1350 x 1080
YY, XX = np.mgrid[0:PH, 0:PW].astype(np.float32)


# --------------------------------------------------------------------------
# Timing helpers
# --------------------------------------------------------------------------
def clamp01(u):
    return max(0.0, min(1.0, u))


def ease(u):
    u = clamp01(u)
    return u * u * u * (u * (6 * u - 15) + 10)


def ramp(t, t0, t1):
    return ease((t - t0) / (t1 - t0))


def bump(t, t0, d):
    u = (t - t0) / d
    return math.sin(math.pi * u) if 0 < u < 1 else 0.0


def damped(t, t0, d=0.9, f=1.4):
    """Elegant pop with a small settle; peak normalised to ~1."""
    u = (t - t0) / d
    if u <= 0 or u >= 1:
        return 0.0
    return math.exp(-3.2 * u) * math.sin(2 * math.pi * f * u) * (1 - u) / 0.52


def ease_out_back(u, k=1.4):
    u = clamp01(u)
    return 1 + (k + 1) * (u - 1) ** 3 + k * (u - 1) ** 2


def interp_keys(t, keys):
    """keys: list of (time, value-tuple). Eased between keys, held outside."""
    if t <= keys[0][0]:
        return keys[0][1]
    for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
        if t <= t1:
            e = ease((t - t0) / (t1 - t0)) if t1 > t0 else 1.0
            return tuple(a + (b - a) * e for a, b in zip(v0, v1))
    return keys[-1][1]


# --------------------------------------------------------------------------
# Weight masks (poster coordinates)
# --------------------------------------------------------------------------
def ell_w(cx, cy, rx, ry, r0=0.5):
    r = np.sqrt(((XX - cx) / rx) ** 2 + ((YY - cy) / ry) ** 2)
    u = np.clip((1 - r) / (1 - r0), 0, 1)
    return (u * u * (3 - 2 * u)).astype(np.float32)


def poly_mask(pts):
    m = np.zeros((PH, PW), np.float32)
    cv2.fillPoly(m, [np.array(pts, np.int32)], 1.0)
    return m


def poly_w(pts, feather=10):
    """Soft weight that is 1 inside and reaches 0 exactly at the polygon edge."""
    m = poly_mask(pts)
    k = max(1, feather // 2)
    m = cv2.erode(m, np.ones((2 * k + 1, 2 * k + 1), np.uint8))
    return cv2.GaussianBlur(m, (0, 0), feather / 3.0)


def xgate(x0, width=14):
    """0 left of x0, 1 from x0+width: keeps warps off neighbouring text."""
    g = np.clip((XX - x0) / width, 0, 1)
    return g * g * (3 - 2 * g)


def bbox_slice(w, thr=1e-3):
    ys, xs = np.where(w > thr)
    return (slice(ys.min(), ys.max() + 1), slice(xs.min(), xs.max() + 1))


# --------------------------------------------------------------------------
# Timeline
# --------------------------------------------------------------------------
FULL = (540.0, 675.0, 0.94)
CAM_KEYS = [
    (0.0, FULL),
    (0.9, (540.0, 675.0, 0.955)),
    (2.7, (345.0, 600.0, 1.60)),       # push-in on the title
    (3.4, (345.0, 600.0, 1.63)),
    (4.1, (360.0, 625.0, 1.50)),       # step 1
    (5.7, (640.0, 625.0, 1.50)),
    (6.0, (645.0, 625.0, 1.51)),
    (6.7, (360.0, 745.0, 1.50)),       # step 2
    (8.4, (715.0, 745.0, 1.50)),
    (8.8, (718.0, 745.0, 1.51)),
    (9.5, (360.0, 830.0, 1.50)),       # step 3
    (10.7, (715.0, 830.0, 1.50)),
    (11.6, (718.0, 830.0, 1.52)),
    (12.7, FULL),                      # zoom-out to the whole poster
    (15.0, (540.0, 675.0, 0.955)),
]

# Which poster row is in focus (title, step1, step2, step3)
FOCUS_KEYS = [
    (0.0, (1, 0, 0, 0)),
    (3.4, (1, 0, 0, 0)),
    (4.0, (0, 1, 0, 0)),
    (6.0, (0, 1, 0, 0)),
    (6.6, (0, 0, 1, 0)),
    (8.8, (0, 0, 1, 0)),
    (9.4, (0, 0, 0, 1)),
    (15.0, (0, 0, 0, 1)),
]


def dof_strength(t):
    return ramp(t, 1.0, 2.5) * (1 - ramp(t, 11.6, 12.5))


SYNC = 12.8  # synchronized "all together" beat on the full poster


def scene_env(t, t0, t1, fade=0.5):
    """1 while a scene is on screen, eased at both ends, 0.35 elsewhere."""
    return 0.35 + 0.65 * ramp(t, t0 - fade, t0) * (1 - ramp(t, t1, t1 + fade))


# --------------------------------------------------------------------------
# Warp fields: (weight, slice, function(t) -> kind, params)
# --------------------------------------------------------------------------
TAU = 2 * math.pi
DEG = math.pi / 180


def make_fields():
    f = []

    def add(w, fn):
        f.append((w, bbox_slice(w), fn))

    sync_nod = lambda t: bump(t, SYNC, 1.0)

    # --- Scene 1: title + pill pop, background float -----------------------
    title_w = poly_w([(10, 165), (612, 165), (612, 384), (10, 384)], 30)
    add(title_w, lambda t: ("scale", (305, 280),
                            1 + 0.055 * damped(t, 2.05, 1.0)
                            + 0.025 * damped(t, SYNC, 1.0)))
    pill_w = poly_w([(40, 384), (578, 372), (580, 455), (42, 462)], 14)
    add(pill_w, lambda t: ("scale", (310, 418),
                           1 + 0.05 * damped(t, 2.75, 0.9)
                           + 0.025 * damped(t, SYNC + 0.08, 1.0)))
    bld_w = ell_w(905, 300, 190, 165, 0.3)
    add(bld_w, lambda t: ("trans", -2.5 * math.sin(TAU * t / 7.5),
                          1.6 * math.sin(TAU * t / 5.0)))

    # --- Scene 2: step 1 character + documents ----------------------------
    e1 = lambda t: scene_env(t, 3.8, 6.0)
    add(ell_w(673, 505, 88, 95, 0.55),
        lambda t: ("rot", (673, 612),
                   (1.3 * math.sin(TAU * t / 2.8) * e1(t) + 1.2 * sync_nod(t)) * DEG))
    add(ell_w(675, 695, 120, 70, 0.4),
        lambda t: ("trans", 0.0, 1.2 * math.sin(TAU * t / 3.6) * e1(t)))
    surat = [(456, 512), (583, 515), (576, 700), (470, 706), (436, 702), (436, 604), (452, 598)]
    add(poly_w(surat, 10) * xgate(450, 6),
        lambda t: ("rot", (455, 662),
                   (0.9 * math.sin(TAU * t / 3.1 + 0.8) * e1(t) - 0.6 * sync_nod(t)) * DEG))
    kk = [(764, 562), (1000, 590), (1006, 700), (990, 748), (760, 745)]
    add(poly_w(kk, 10),
        lambda t: ("rot", (986, 706),
                   (-0.7 * math.sin(TAU * t / 3.4 + 1.7) * e1(t) + 0.5 * sync_nod(t)) * DEG))

    # --- Scene 3: step 2 applicant, officer, form -------------------------
    e2 = lambda t: scene_env(t, 6.5, 8.8)
    add(ell_w(560, 835, 70, 68, 0.5),
        lambda t: ("rot", (560, 905),
                   (1.0 * math.sin(TAU * t / 3.0 + 0.3) * e2(t) + 0.9 * sync_nod(t)) * DEG))
    add(ell_w(570, 978, 48, 34, 0.45),
        lambda t: ("trans", 1.5 * math.sin(TAU * 2.2 * t) * e2(t),
                   0.9 * math.sin(TAU * 4.4 * t + 0.6) * e2(t)))
    add(ell_w(775, 828, 70, 68, 0.5),
        lambda t: ("rot", (775, 902),
                   (-1.1 * math.sin(TAU * t / 2.6 + 1.2) * e2(t) - 0.9 * sync_nod(t)) * DEG))
    add(ell_w(680, 966, 32, 24, 0.4),
        lambda t: ("trans", 0.0,
                   1.6 * max(0.0, math.sin(TAU * 1.5 * t)) * e2(t)))
    clip_w = poly_w([(903, 766), (1066, 766), (1066, 960), (903, 960)], 10)
    add(clip_w, lambda t: ("rot", (985, 862),
                           (0.8 * math.sin(TAU * t / 2.4 + 0.4) * e2(t)
                            + 0.8 * sync_nod(t)) * DEG))
    add(clip_w, lambda t: ("trans", 0.0,
                           -2.0 * math.sin(TAU * t / 2.4) * e2(t) - 2.5 * sync_nod(t)))

    # --- Scene 4: step 3 thumbs-up, camera pulse, shield ------------------
    e3 = lambda t: scene_env(t, 9.3, 11.6)
    add(ell_w(478, 1150, 115, 120, 0.5) * xgate(380, 20),
        lambda t: ("trans", 0.6 * math.sin(TAU * t / 3.2) * e3(t),
                   -1.4 * (0.5 - 0.5 * math.cos(TAU * 1.1 * t)) * e3(t)
                   - 1.5 * sync_nod(t)))
    add(poly_w([(390, 1108), (462, 1108), (462, 1206), (390, 1206)], 12) * xgate(383, 8),
        lambda t: ("trans", 0.0,
                   -2.4 * (0.5 - 0.5 * math.cos(TAU * 1.1 * t)) * e3(t)
                   - 2.0 * sync_nod(t)))
    cam_w = ell_w(662, 1134, 108, 108, 0.84)
    add(cam_w, lambda t: ("scale", (662, 1134), 1 + 0.045 * camera_pulse(t)))
    shield_w = ell_w(955, 1198, 62, 62, 0.75) * np.clip((YY - 1146) / 6, 0, 1)
    add(shield_w, lambda t: ("scale", (955, 1200),
                             1 + 0.03 * bump(t, 10.9, 0.8) + 0.03 * bump(t, SYNC, 1.0)))
    return f


def camera_pulse(t):
    p = 0.0
    if 9.7 <= t <= 11.7:
        p = 0.5 - 0.5 * math.cos(TAU * (t - 9.7) / 0.95)
    return p + bump(t, SYNC, 1.0)


FIELDS = make_fields()


def warp(img, t):
    dx = np.zeros((PH, PW), np.float32)
    dy = np.zeros((PH, PW), np.float32)
    for w, sl, fn in FIELDS:
        kind, *p = fn(t)
        ww = w[sl]
        if kind == "trans":
            ax, ay = p
            if ax == 0 and ay == 0:
                continue
            dx[sl] += ww * ax
            dy[sl] += ww * ay
        elif kind == "rot":
            (px, py), th = p
            if th == 0:
                continue
            c, s = math.cos(th), math.sin(th)
            X = XX[sl] - px
            Y = YY[sl] - py
            dx[sl] += ww * ((c - 1) * X - s * Y)
            dy[sl] += ww * (s * X + (c - 1) * Y)
        elif kind == "scale":
            (cx, cy), sc = p
            if sc == 1:
                continue
            dx[sl] += ww * (sc - 1) * (XX[sl] - cx)
            dy[sl] += ww * (sc - 1) * (YY[sl] - cy)
    out = cv2.remap(img, XX - dx, YY - dy, cv2.INTER_CUBIC,
                    borderMode=cv2.BORDER_REFLECT)
    return out, dx, dy


# --------------------------------------------------------------------------
# Blinks (eyelid overlay; eye art itself is untouched)
# --------------------------------------------------------------------------
EYES = {
    # face: [(cx, cy, w, h), ...]
    "step1": [(647.5, 534.0, 25, 23), (696.5, 532.0, 25, 23)],
    "applicant": [(557.5, 839.5, 17, 17), (582.0, 845.0, 12, 13)],
    "officer": [(749.5, 829.5, 13, 15), (779.0, 833.0, 18, 14)],
    "step3": [(480.0, 1098.5, 22, 21), (525.0, 1091.5, 22, 21)],
}
BLINKS = {
    "step1": [1.4, 4.5, 5.9, SYNC + 0.35, 14.3],
    "applicant": [2.2, 7.0, 8.3, SYNC + 0.35],
    "officer": [3.0, 7.6, SYNC + 0.35, 14.0],
    "step3": [3.6, 9.9, 11.2, SYNC + 0.35, 14.5],
}
LASH = np.array([0.12, 0.11, 0.14], np.float32)  # BGR, dark brown


def surface_fit(y0, y1, x0, x1, ring, accept, deg=1):
    """Least-squares colour surface through the accepted ring pixels of a box."""
    roi = SRC[y0:y1, x0:x1]
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    sel = ring & accept(roi)
    u = (xx - x0) / max(1, x1 - x0)
    v = (yy - y0) / max(1, y1 - y0)
    terms = [np.ones_like(u), u, v] + ([u * u, v * v, u * v] if deg > 1 else [])
    A = np.stack([t[sel] for t in terms], 1)
    coef, *_ = np.linalg.lstsq(A, roi[sel], rcond=None)
    B = np.stack([t.ravel() for t in terms], 1)
    return (B @ coef).reshape(y1 - y0, x1 - x0, 3).astype(np.float32)


def skin_like(r):
    b, g, rr = r[..., 0], r[..., 1], r[..., 2]
    return (rr > 0.55) & (rr > g) & (g > b) & (rr - b > 0.12) & (g > 0.35)


def eyelid_skin(cx, cy, ew, eh):
    """Skin surface fitted to the ring around an eye, used as the closed lid."""
    a, b = ew / 2 + 1.5, eh / 2 + 1.5
    x0, x1 = int(cx - a - 9), int(cx + a + 10)
    y0, y1 = int(cy - b - 9), int(cy + b + 10)
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    r = np.sqrt(((xx - cx) / a) ** 2 + ((yy - cy) / b) ** 2)
    ring = (r > 1.2) & (r < 1.9)
    roi = SRC[y0:y1, x0:x1]
    lum = roi.mean(axis=2)
    ok = ring & skin_like(roi)
    # ignore the shadowed part of the ring (lash/brow shade) so the lid does
    # not read darker than the surrounding skin
    cut = np.percentile(lum[ok], 45)
    return (x0, y0), surface_fit(y0, y1, x0, x1, ring,
                                 lambda q: skin_like(q) & (q.mean(axis=2) >= cut), 1)


EYE_SKIN = {k: [eyelid_skin(*e) for e in v] for k, v in EYES.items()}


def blink_amount(t, times, d=0.22):
    best = 0.0
    for t0 in times:
        u = (t - t0) / d
        if 0 <= u <= 1:
            p = u / 0.35 if u < 0.35 else 1 - (u - 0.35) / 0.65
            best = max(best, ease(p))
    return best


def draw_blink(img, cx, cy, ew, eh, p, skin, S=4):
    (sx0, sy0), lid_col = skin
    a = ew / 2 + 1.5
    b = eh / 2 + 1.5
    x0, x1 = int(math.floor(cx - a - 2)), int(math.ceil(cx + a + 2))
    y0, y1 = int(math.floor(cy - b - 2)), int(math.ceil(cy + b + 2))
    gx = (np.arange((x1 - x0) * S) + 0.5) / S + x0
    gy = (np.arange((y1 - y0) * S) + 0.5) / S + y0
    GX, GY = np.meshgrid(gx, gy)
    u = (GX - cx) / a
    v = (GY - cy) / b
    rr = np.sqrt(u * u + v * v)
    inside = np.clip((1.0 - rr) * b / 1.2 + 0.5, 0, 1)  # ~1px soft rim
    root = np.sqrt(np.clip(1 - u * u, 0, 1))
    top, bot = cy - b * root, cy + b * root
    meet = cy + 0.3 * b * root
    edge_u = top + (meet - top) * p
    edge_l = bot - (bot - meet) * p
    lid = inside * (((GY < edge_u) | (GY > edge_l)).astype(np.float32))
    thick = 0.45 + 0.45 * root
    line = ((np.abs(GY - edge_u) < thick) & (np.abs(u) < 0.98)).astype(np.float32)
    line *= (rr < 1.05)
    crease = np.clip(1 - np.abs(GY - edge_u) / (0.45 * b), 0, 1) * (GY < edge_u)

    def down(m):
        return m.reshape(y1 - y0, S, x1 - x0, S).mean(axis=(1, 3))

    A = down(lid)[..., None]
    L = (down(line) * min(1.0, p * 2.5))[..., None]
    C = (down(crease * lid) * 0.10 * p)[..., None]
    col = lid_col[y0 - sy0:y1 - sy0, x0 - sx0:x1 - sx0] * (1 - C)
    roi = img[y0:y1, x0:x1]
    roi[:] = roi * (1 - A) + col * A
    roi[:] = roi * (1 - L) + LASH * L


# --------------------------------------------------------------------------
# Header logos (fade-in) and speech bubble (pop-up)
# --------------------------------------------------------------------------
HEADER_BG = np.array([255, 212, 133], np.float32) / 255.0  # BGR of the band
HDR_H = 121
hdr = SRC[:HDR_H]
logo_m = (np.abs(hdr - HEADER_BG).max(axis=2) > 18 / 255.0).astype(np.float32)
logo_m = cv2.dilate(logo_m, np.ones((5, 5), np.uint8))
logo_m = cv2.GaussianBlur(logo_m, (0, 0), 0.8)
LOGO_SPLITS = [(0, 217), (217, 348), (348, 607), (607, 810), (810, 1080)]
HEADER_T0 = 0.45  # first logo starts fading in


def draw_header(img, t):
    out = img[:HDR_H]
    base = out * (1 - logo_m[..., None]) + HEADER_BG * logo_m[..., None]
    res = base.copy()
    for i, (xa, xb) in enumerate(LOGO_SPLITS):
        a = ramp(t, HEADER_T0 + 0.22 * i, HEADER_T0 + 0.7 + 0.22 * i)
        if a <= 0:
            continue
        rise = (1 - a) * 12.0
        M = np.float32([[1, 0, 0], [0, 1, rise]])
        seg = np.ascontiguousarray(out[:, xa:xb])
        segm = np.ascontiguousarray(logo_m[:, xa:xb])
        s_img = cv2.warpAffine(seg, M, (xb - xa, HDR_H), flags=cv2.INTER_CUBIC,
                               borderMode=cv2.BORDER_REPLICATE)
        s_m = cv2.warpAffine(segm, M, (xb - xa, HDR_H), flags=cv2.INTER_LINEAR) * a
        res[:, xa:xb] = res[:, xa:xb] * (1 - s_m[..., None]) + s_img * s_m[..., None]
    img[:HDR_H] = res


BUBBLE_PTS = [(808, 1050), (1047, 1050), (1047, 1142), (899, 1142), (889, 1156),
              (878, 1142), (808, 1142)]
bub_hard = np.zeros((PH, PW), np.uint8)
cv2.fillPoly(bub_hard, [np.array(BUBBLE_PTS, np.int32)], 255)
bub_hard = cv2.dilate(bub_hard, np.ones((9, 9), np.uint8))
BUB_Y0, BUB_Y1, BUB_X0, BUB_X1 = 1030, 1170, 790, 1066
_bsl = (slice(BUB_Y0, BUB_Y1), slice(BUB_X0, BUB_X1))
_bring = (cv2.dilate(bub_hard, np.ones((25, 25), np.uint8))
          - cv2.dilate(bub_hard, np.ones((7, 7), np.uint8)))[_bsl] > 0
# the card behind the bubble is a smooth light gradient: refit it, skipping
# the blue shield/border pixels
BUBBLE_EMPTY = SRC.copy()
BUBBLE_EMPTY[_bsl] = surface_fit(
    BUB_Y0, BUB_Y1, BUB_X0, BUB_X1, _bring,
    lambda r: (r.min(axis=2) > 0.72) & (r[..., 0] - r[..., 2] < 0.2), 2)
_shield_keep = np.zeros((PH, PW), np.uint8)
_shield_keep[1143:1253, 895:1016] = (
    (SRC[1143:1253, 895:1016, 0] > 0.55) & (SRC[1143:1253, 895:1016, 2] < 0.5)) * 255
_shield_keep = cv2.dilate(_shield_keep, np.ones((5, 5), np.uint8))
bub_hard[_shield_keep > 0] = 0
BUB_SOFT = cv2.GaussianBlur(bub_hard.astype(np.float32) / 255.0, (0, 0), 1.5)
BUB_TAIL = (889.0, 1156.0)
BUB_HIDE, BUB_POP = 9.4, 10.35


def draw_bubble(img, t):
    if t < BUB_HIDE:
        return
    sl = (slice(BUB_Y0, BUB_Y1), slice(BUB_X0, BUB_X1))
    m = BUB_SOFT[sl][..., None]
    img[sl] = img[sl] * (1 - m) + BUBBLE_EMPTY[sl] * m
    u = (t - BUB_POP) / 0.55
    if u <= 0:
        return
    s = 0.35 + 0.65 * ease_out_back(u, 1.2)
    s *= 1 + 0.03 * damped(t, SYNC, 1.0)
    alpha = ramp(t, BUB_POP, BUB_POP + 0.22)
    tx, ty = BUB_TAIL[0] - BUB_X0, BUB_TAIL[1] - BUB_Y0
    M = np.float32([[s, 0, tx - s * tx], [0, s, ty - s * ty]])
    size = (BUB_X1 - BUB_X0, BUB_Y1 - BUB_Y0)
    layer = cv2.warpAffine(np.ascontiguousarray(SRC[sl]), M, size, flags=cv2.INTER_CUBIC,
                           borderMode=cv2.BORDER_REPLICATE)
    lm = cv2.warpAffine(np.ascontiguousarray(BUB_SOFT[sl]), M, size,
                        flags=cv2.INTER_LINEAR)[..., None] * alpha
    img[sl] = img[sl] * (1 - lm) + layer * lm


# --------------------------------------------------------------------------
# Highlights: light sweeps and glows
# --------------------------------------------------------------------------
M_SURAT = poly_mask([(458, 514), (581, 517), (574, 698), (452, 690)])
M_KK = poly_mask([(768, 566), (998, 594), (1000, 700), (985, 746), (762, 742)])
M_CLIP = poly_mask([(906, 768), (1062, 768), (1062, 958), (906, 958)])
M_PAPER = poly_mask([(600, 972), (765, 960), (790, 1016), (610, 1018)])


def color_mask(x0, y0, x1, y1, fn):
    m = np.zeros((PH, PW), np.float32)
    roi = SRC[y0:y1, x0:x1]
    m[y0:y1, x0:x1] = fn(roi).astype(np.float32)
    return m


# shield: saturated blue pixels
M_SHIELD = color_mask(895, 1143, 1016, 1253,
                      lambda r: (r[..., 0] > 0.55) & (r[..., 2] < 0.45) & (r[..., 1] < 0.65))
M_SHIELD = cv2.morphologyEx(M_SHIELD, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
# no-photo sign: red ring pixels
M_RED = color_mask(565, 1038, 762, 1230,
                   lambda r: (r[..., 2] > 0.7) & (r[..., 1] < 0.5) & (r[..., 0] < 0.5))


def outer_glow(mask, sigma):
    g = cv2.GaussianBlur(mask, (0, 0), sigma)
    g = g / max(g.max(), 1e-4)
    return np.clip(g * (1 - mask), 0, 1)


G_SHIELD = outer_glow(M_SHIELD, 11)
G_RED = outer_glow(M_RED, 8)
G_CLIP = outer_glow(M_CLIP, 10)
G_PAPER = outer_glow(M_PAPER, 8)

SWEEP_DIR = np.array([0.88, 0.47], np.float32)


def sweep(img, mask, t, t0, dur=0.85, strength=0.42, width=26.0):
    u = (t - t0) / dur
    if not 0 < u < 1:
        return
    ys, xs = np.where(mask > 0.01)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    proj = XX[y0:y1, x0:x1] * SWEEP_DIR[0] + YY[y0:y1, x0:x1] * SWEEP_DIR[1]
    pmin, pmax = proj.min() - width * 2, proj.max() + width * 2
    pos = pmin + (pmax - pmin) * ease(u)
    band = np.exp(-((proj - pos) / width) ** 2) * strength * mask[y0:y1, x0:x1]
    band *= math.sin(math.pi * u) ** 0.5
    roi = img[y0:y1, x0:x1]
    roi[:] = 1 - (1 - roi) * (1 - band[..., None])


def glow(img, g, color, a):
    if a <= 0.001:
        return
    ys, xs = np.where(g > 0.01)
    sl = (slice(ys.min(), ys.max() + 1), slice(xs.min(), xs.max() + 1))
    A = (g[sl] * a)[..., None]
    img[sl] = img[sl] * (1 - A) + np.array(color, np.float32) * A


WARM = (0.70, 0.93, 1.00)   # BGR soft warm-white
BLUE = (1.00, 0.84, 0.55)   # BGR soft blue
RED = (0.45, 0.45, 1.00)    # BGR soft red


def highlights(img, t):
    # step 1 documents
    sweep(img, M_SURAT, t, 4.25)
    sweep(img, M_KK, t, 5.35)
    # step 2 form on desk + clipboard
    glow(img, G_PAPER, WARM, 0.55 * bump(t, 7.0, 1.4))
    sweep(img, M_PAPER, t, 7.15, strength=0.35)
    glow(img, G_CLIP, WARM, 0.6 * bump(t, 7.9, 1.6))
    sweep(img, M_CLIP, t, 8.1)
    # step 3 camera pulse + shield glow
    glow(img, G_RED, RED, 0.35 * camera_pulse(t))
    sh = 0.0
    if t > 10.6:
        sh = ramp(t, 10.6, 11.0) * (0.75 + 0.25 * math.sin(TAU * (t - 10.6) / 1.3))
        sh *= 1 - 0.5 * ramp(t, 11.6, 12.4)
    sh = max(sh, bump(t, SYNC, 1.0))
    glow(img, G_SHIELD, BLUE, 0.6 * sh)
    sweep(img, M_SHIELD, t, 10.75, strength=0.35)
    # synchronized beat
    for m in (M_SURAT, M_KK, M_CLIP, M_SHIELD):
        sweep(img, m, t, SYNC + 0.1, dur=0.9, strength=0.3)


# --------------------------------------------------------------------------
# Depth of field on the non-active rows
# --------------------------------------------------------------------------
def row_mask(y0, y1, f=22):
    y = np.arange(PH, dtype=np.float32)
    m = np.clip((y - y0) / f + 0.5, 0, 1) * np.clip((y1 - y) / f + 0.5, 0, 1)
    return np.repeat(m[:, None], PW, axis=1)


ROWS = [row_mask(-100, 455), row_mask(430, 754), row_mask(754, 1016), row_mask(1016, 1256)]
VEIL = np.array([1.0, 0.975, 0.95], np.float32)


def depth_of_field(img, t):
    s = dof_strength(t)
    if s <= 0.001:
        return img
    wts = interp_keys(t, FOCUS_KEYS)
    M = np.zeros((PH, PW), np.float32)
    for w, r in zip(wts, ROWS):
        if w > 0:
            M = np.maximum(M, r * w)
    bw = (s * (1 - M))[..., None]
    blur = cv2.GaussianBlur(img, (0, 0), 3.2)
    blur = blur * 0.82 + VEIL * 0.18
    return img * (1 - bw) + blur * bw


# --------------------------------------------------------------------------
# Output composition: background, card shadow, camera
# --------------------------------------------------------------------------
def rounded_alpha(w, h, r, S=4):
    m = np.zeros((h * S, w * S), np.uint8)
    R = r * S
    cv2.rectangle(m, (R, 0), (w * S - R, h * S), 255, -1)
    cv2.rectangle(m, (0, R), (w * S, h * S - R), 255, -1)
    for cx, cy in ((R, R), (w * S - R, R), (R, h * S - R), (w * S - R, h * S - R)):
        cv2.circle(m, (cx, cy), R, 255, -1)
    return cv2.resize(m, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0


CARD_A = rounded_alpha(PW, PH, 22)
PAD = 140
SHADOW = np.zeros((PH + 2 * PAD, PW + 2 * PAD), np.float32)
SHADOW[PAD + 14:PAD + 14 + PH, PAD:PAD + PW] = CARD_A
SHADOW = cv2.GaussianBlur(SHADOW, (0, 0), 26)
SHADOW_COL = np.array([0.35, 0.22, 0.10], np.float32)  # deep navy (BGR)

BG_W, BG_H = 1600, 2400
_s = max(BG_W / PW, BG_H / PH)
_bg = cv2.resize(SRC, (int(PW * _s) + 1, int(PH * _s) + 1), interpolation=cv2.INTER_AREA)
_oy, _ox = (_bg.shape[0] - BG_H) // 2, (_bg.shape[1] - BG_W) // 2
_bg = _bg[_oy:_oy + BG_H, _ox:_ox + BG_W]
_bg = cv2.GaussianBlur(_bg, (0, 0), 55)
BG = _bg * 0.62 + np.array([1.0, 0.95, 0.88], np.float32) * 0.38


def background(t, cx, cy, z):
    zb = 1 + 0.07 * (z - FULL[2])
    bx = -(cx - 540) * 0.10 + 10 * math.sin(TAU * t / 9.0)
    by = -(cy - 675) * 0.10 + 12 * math.sin(TAU * t / 7.0 + 1.0)
    M = np.float32([[zb, 0, W / 2 - zb * (BG_W / 2 - bx)],
                    [0, zb, H / 2 - zb * (BG_H / 2 - by)]])
    return cv2.warpAffine(BG, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def compose(poster, t):
    cx, cy, z = interp_keys(t, CAM_KEYS)
    # intro: card settles in; sync beat: card breathes once
    intro = ramp(t, 0.0, 0.8)
    zc = z * (0.965 + 0.035 * intro) * (1 + 0.012 * bump(t, SYNC, 1.0))
    frame = background(t, cx, cy, z)

    M = np.float32([[zc, 0, W / 2 - zc * cx], [0, zc, H / 2 - zc * cy]])
    Ms = M.copy()
    Ms[:, 2] -= zc * PAD
    sh = cv2.warpAffine(SHADOW, Ms, (W, H), flags=cv2.INTER_LINEAR)[..., None] * 0.30 * intro
    frame = frame * (1 - sh) + SHADOW_COL * sh

    p = cv2.warpAffine(poster, M, (W, H), flags=cv2.INTER_LANCZOS4,
                       borderMode=cv2.BORDER_CONSTANT)
    a = cv2.warpAffine(CARD_A, M, (W, H), flags=cv2.INTER_LINEAR)[..., None] * intro
    frame = frame * (1 - a) + p * a
    if zc > 1.2:  # gentle sharpening to offset the upscale softness
        k = min(1.0, (zc - 1.2) / 0.3) * 0.35
        soft = cv2.GaussianBlur(frame, (0, 0), 1.1)
        frame = frame + (frame - soft) * k
    return frame


def render_frame(t):
    img, dx, dy = warp(SRC, t)
    draw_header(img, t)
    draw_bubble(img, t)
    for face, eyes in EYES.items():
        p = blink_amount(t, BLINKS[face])
        if p <= 0.01:
            continue
        for (ex, ey, ew, eh), skin in zip(eyes, EYE_SKIN[face]):
            ix, iy = int(round(ex)), int(round(ey))
            draw_blink(img, ex + dx[iy, ix], ey + dy[iy, ix], ew, eh, p, skin)
    highlights(img, t)
    img = depth_of_field(img, t)
    np.clip(img, 0, 1, out=img)
    frame = compose(img, t)
    return (np.clip(frame, 0, 1) * 255 + 0.5).astype(np.uint8)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = args[0] if args else os.path.join(HERE, "ktp-el-hilang-9x16.mp4")
    if "--preview" in sys.argv:
        pdir = os.path.join(os.path.dirname(os.path.abspath(out)), "preview")
        os.makedirs(pdir, exist_ok=True)
        for t in [0.3, 1.2, 2.3, 3.0, 4.5, 5.4, 7.2, 8.2, 9.9, 10.5, 11.2, 12.2, 13.1, 14.5]:
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
