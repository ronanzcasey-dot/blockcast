#!/usr/bin/env python3
"""
blockcast — stream your screen into Minecraft in real time using colored blocks.
macOS only (uses Quartz for window capture).

Stand where you want the canvas, run /screen in-game, then run this script.
"""

import ctypes
import time
import sys
import signal
import threading
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from mcrcon import MCRcon
import mss
from PIL import Image
from Quartz import (CGWindowListCopyWindowInfo, kCGNullWindowID,
                    CGWindowListCreateImage, CGRectInfinite,
                    CGImageGetWidth, CGImageGetHeight,
                    CGColorSpaceCreateDeviceRGB, CGBitmapContextCreate,
                    CGContextDrawImage, CGRectMake,
                    kCGWindowListOptionIncludingWindow, kCGWindowImageBoundsIgnoreFraming)

RCON_HOST     = "127.0.0.1"
RCON_PORT     = 25575
RCON_PASSWORD = "yourpassword"   # set this to your rcon.password in server.properties
PLAYER        = "YourUsername"   # your Minecraft username

SCREEN_W    = 160
SCREEN_H    = 90
TARGET_FPS  = 30           # cap; actual fps depends on server throughput
NUM_WORKERS = 200          # parallel RCON connections

# Color quantization: reduce 8-bit channels to QUANT_BITS bits for LUT indexing
# 5 bits = 32 levels per channel = 32768 LUT entries (built at startup, ~instant lookup)
QUANT_BITS  = 5
QUANT_SHIFT = 8 - QUANT_BITS
LUT_SIZE    = 1 << QUANT_BITS  # 32

# Colors extracted from 1.21.11 jar textures (average of opaque pixels, no biome-tinted blocks)
PALETTE = {
    "white_concrete":           (207, 213, 214),
    "orange_concrete":          (224,  97,   0),
    "magenta_concrete":         (169,  48, 159),
    "light_blue_concrete":      ( 35, 137, 198),
    "yellow_concrete":          (240, 175,  21),
    "lime_concrete":            ( 94, 168,  24),
    "pink_concrete":            (213, 101, 142),
    "gray_concrete":            ( 54,  57,  61),
    "light_gray_concrete":      (125, 125, 115),
    "cyan_concrete":            ( 21, 119, 136),
    "purple_concrete":          (100,  31, 156),
    "blue_concrete":            ( 44,  46, 143),
    "brown_concrete":           ( 96,  59,  31),
    "green_concrete":           ( 73,  91,  36),
    "red_concrete":             (142,  32,  32),
    "black_concrete":           (  8,  10,  15),
    "terracotta":               (152,  94,  67),
    "white_terracotta":         (209, 178, 161),
    "orange_terracotta":        (161,  83,  37),
    "yellow_terracotta":        (186, 133,  35),
    "red_terracotta":           (143,  61,  46),
    "brown_terracotta":         ( 77,  51,  35),
    "green_terracotta":         ( 76,  83,  42),
    "cyan_terracotta":          ( 86,  91,  91),
    "gray_terracotta":          ( 57,  42,  35),
    "light_gray_terracotta":    (135, 106,  97),
    "pink_terracotta":          (161,  78,  78),
    "purple_terracotta":        (118,  70,  86),
    "blue_terracotta":          ( 74,  59,  91),
    "lime_terracotta":          (103, 117,  52),
    "magenta_terracotta":       (149,  88, 108),
    "light_blue_terracotta":    (113, 108, 137),
    "snow_block":               (249, 254, 254),
    "coal_block":               ( 16,  15,  15),
    "iron_block":               (220, 220, 220),
    "gold_block":               (246, 208,  61),
    "diamond_block":            ( 98, 237, 228),
    "emerald_block":            ( 42, 203,  87),
    "lapis_block":              ( 30,  67, 140),
    "netherrack":               ( 97,  38,  38),
    "soul_sand":                ( 81,  62,  50),
    "dirt":                     (134,  96,  67),
    "obsidian":                 ( 15,  10,  24),
    "purpur_block":             (169, 125, 169),
    "quartz_block":             (235, 229, 222),
    "prismarine":               ( 99, 156, 151),
    "dark_prismarine":          ( 51,  91,  75),
    "end_stone":                (219, 222, 158),
    "nether_bricks":            ( 44,  21,  26),
}

PALETTE_NAMES = list(PALETTE.keys())
_PAL_NP       = np.array(list(PALETTE.values()), dtype=np.float32)


def rgb_to_lab(rgb):
    """Convert Nx3 float32 array (0-255) to CIE L*a*b* (vectorized)."""
    # Normalize to 0-1
    c = rgb / 255.0
    # sRGB linearize
    mask = c > 0.04045
    c[mask]  = ((c[mask]  + 0.055) / 1.055) ** 2.4
    c[~mask] = c[~mask] / 12.92
    # Linear RGB -> XYZ (D65)
    M = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]], dtype=np.float32)
    xyz = c @ M.T
    # Normalize by D65 white point
    xyz /= np.array([0.95047, 1.00000, 1.08883], dtype=np.float32)
    # XYZ -> Lab
    eps = 0.008856
    kap = 903.3
    mask2 = xyz > eps
    f = np.where(mask2, np.cbrt(xyz), (kap * xyz + 16.0) / 116.0)
    L = 116.0 * f[:, 1] - 16.0
    a = 500.0 * (f[:, 0] - f[:, 1])
    b = 200.0 * (f[:, 1] - f[:, 2])
    return np.stack([L, a, b], axis=-1)


def build_lut():
    """Precompute 32x32x32 LUT mapping quantized RGB -> palette index using LAB distance."""
    print("Building LAB color LUT...", end=" ", flush=True)
    # Center value for each quantized level
    levels = np.arange(LUT_SIZE, dtype=np.float32) * (1 << QUANT_SHIFT) + (1 << (QUANT_SHIFT - 1))
    rr, gg, bb = np.meshgrid(levels, levels, levels, indexing='ij')
    pixels_rgb = np.stack([rr, gg, bb], axis=-1).reshape(-1, 3)   # (32768, 3)

    pixels_lab = rgb_to_lab(pixels_rgb.copy())                     # (32768, 3)
    palette_lab = rgb_to_lab(_PAL_NP.copy())                       # (N, 3)

    diffs = pixels_lab[:, None, :] - palette_lab[None, :, :]      # (32768, N, 3)
    dists = np.sum(diffs ** 2, axis=-1)                            # (32768, N)

    lut = np.argmin(dists, axis=-1).reshape(LUT_SIZE, LUT_SIZE, LUT_SIZE).astype(np.uint8)
    print("done.")
    return lut


def pick_window():
    """
    List visible windows and let user pick one.
    Returns (window_id, label) or (None, None) for full screen.
    """
    wins = CGWindowListCopyWindowInfo(0, kCGNullWindowID)  # 0 = all windows incl. other Spaces
    visible = []
    for w in wins:
        name   = w.get("kCGWindowOwnerName", "")
        title  = w.get("kCGWindowName", "")
        bounds = w.get("kCGWindowBounds", {})
        wid    = w.get("kCGWindowNumber", 0)
        if not name or bounds.get("Width", 0) < 100:
            continue
        visible.append((name, title, bounds, wid))

    seen    = set()
    deduped = []
    for name, title, bounds, wid in visible:
        key = (name, title)
        if key not in seen:
            seen.add(key)
            deduped.append((name, title, bounds, wid))

    print("\nAvailable windows (0 = full screen):")
    print("  0: Full screen")
    for i, (name, title, bounds, wid) in enumerate(deduped, 1):
        label = f"{name}" + (f" — {title}" if title else "")
        print(f"  {i}: {label}  [{int(bounds['Width'])}x{int(bounds['Height'])}]")

    try:
        choice = int(input("\nEnter number: ").strip())
    except (ValueError, EOFError):
        choice = 0

    if choice == 0 or choice > len(deduped):
        print("Using full screen.")
        return None, None

    name, title, bounds, wid = deduped[choice - 1]
    label = f"{name} — {title}"
    print(f"Capturing window: {label}  (id={wid})")
    return wid, label


def grab_window(window_id):
    """Capture a specific window by ID using Quartz — works even when covered."""
    cg_img = CGWindowListCreateImage(
        CGRectInfinite,
        kCGWindowListOptionIncludingWindow,
        window_id,
        kCGWindowImageBoundsIgnoreFraming,
    )
    if cg_img is None:
        return None
    w  = CGImageGetWidth(cg_img)
    h  = CGImageGetHeight(cg_img)
    cs = CGColorSpaceCreateDeviceRGB()
    data = (ctypes.c_uint8 * (w * h * 4))()
    ctx  = CGBitmapContextCreate(data, w, h, 8, w * 4, cs, 0x2002)
    CGContextDrawImage(ctx, CGRectMake(0, 0, w, h), cg_img)
    arr = np.frombuffer(data, dtype=np.uint8).reshape((h, w, 4))
    return arr[:, :, 2::-1].copy()  # BGRA -> RGB


def get_canvas_origin(rcon):
    pos_resp = rcon.command(f"data get entity {PLAYER} Pos")
    rot_resp = rcon.command(f"data get entity {PLAYER} Rotation")
    try:
        pos_str    = pos_resp.split("[")[1].split("]")[0]
        parts      = [p.strip().rstrip("d").rstrip("f") for p in pos_str.split(",")]
        ox, oy, oz = int(float(parts[0])), int(float(parts[1])), int(float(parts[2]))
        rot_str    = rot_resp.split("[")[1].split("]")[0]
        yaw        = float(rot_str.split(",")[0].strip().rstrip("f"))
    except Exception as e:
        print(f"Couldn't read player position. Is {PLAYER} online?\n  {e}")
        sys.exit(1)
    print(f"Canvas origin: ({ox}, {oy}, {oz}), yaw={yaw:.1f}")
    return ox, oy, oz, yaw


def col_to_offset(yaw, col, width):
    center = col - width // 2
    yaw    = yaw % 360
    if   yaw < 45  or yaw >= 315: return  center, 0        # south
    elif yaw < 135:                return  0, -center       # west
    elif yaw < 225:                return -center, 0        # north
    else:                          return  0,  center       # east


def precompute_coords(ox, oy, oz, yaw, width, height):
    col_xz = [(ox + dx, oz + dz) for col in range(width)
               for dx, dz in [col_to_offset(yaw, col, width)]]
    row_y  = [oy + (height - 1 - row) for row in range(height)]
    return col_xz, row_y


def row_to_fill_commands(row_idx, block_indices, col_xz, row_y):
    """RLE-encode one row of palette indices into /fill commands."""
    by    = row_y[row_idx]
    cmds  = []
    width = len(block_indices)
    col   = 0
    while col < width:
        idx     = block_indices[col]
        run_end = col
        while run_end + 1 < width and block_indices[run_end + 1] == idx:
            run_end += 1
        x1, z1 = col_xz[col]
        x2, z2 = col_xz[run_end]
        cmds.append(f"fill {x1} {by} {z1} {x2} {by} {z2} {PALETTE_NAMES[idx]} replace")
        col = run_end + 1
    return cmds


running = True

def handle_exit(sig, frame):
    global running
    running = False
    print("\nStopping...")

signal.signal(signal.SIGINT, handle_exit)


class RconPool:
    def __init__(self, size):
        self._size  = size
        self._conns = []
        self._locks = []
        self._idx_lock = threading.Lock()
        self._idx   = 0
        print(f"Opening {size} RCON connections...", end=" ", flush=True)
        for _ in range(size):
            c = MCRcon(RCON_HOST, RCON_PASSWORD, port=RCON_PORT)
            c.connect()
            self._conns.append(c)
            self._locks.append(threading.Lock())
        print("done.")

    def send(self, cmd):
        with self._idx_lock:
            idx = self._idx % self._size
            self._idx += 1
        with self._locks[idx]:
            try:
                self._conns[idx].command(cmd)
            except Exception:
                try: self._conns[idx].disconnect()
                except: pass
                c = MCRcon(RCON_HOST, RCON_PASSWORD, port=RCON_PORT)
                c.connect()
                self._conns[idx] = c
                c.command(cmd)

    def get_one(self):
        return self._conns[0]

    def close(self):
        for c in self._conns:
            try: c.disconnect()
            except: pass


def main():
    lut = build_lut()

    window_id, _ = pick_window()

    print(f"Resolution: {SCREEN_W}x{SCREEN_H} | Workers: {NUM_WORKERS} | FPS cap: {TARGET_FPS}")
    pool = RconPool(NUM_WORKERS)

    ox, oy, oz, yaw = get_canvas_origin(pool.get_one())
    col_xz, row_y   = precompute_coords(ox, oy, oz, yaw, SCREEN_W, SCREEN_H)

    prev_rows = {}
    frame_gap = 1.0 / TARGET_FPS
    frame_n   = 0

    with mss.MSS() as sct:
        monitor = sct.monitors[1]

        with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
            while running:
                t0 = time.time()

                # --- Capture ---
                if window_id is not None:
                    arr = grab_window(window_id)
                    if arr is None:
                        print("Window lost, falling back to full screen.")
                        window_id = None
                        arr = np.frombuffer(sct.grab(monitor).raw, dtype=np.uint8).reshape((-1, monitor["width"], 4))[:, :, 2::-1]
                else:
                    raw = sct.grab(monitor)
                    arr = np.frombuffer(raw.raw, dtype=np.uint8).reshape((raw.height, raw.width, 4))[:, :, 2::-1]

                img  = Image.fromarray(arr, "RGB").resize((SCREEN_W, SCREEN_H), Image.BILINEAR)
                npix = np.asarray(img, dtype=np.uint8)   # (H, W, 3)

                # --- Vectorized color mapping (entire frame in one numpy op) ---
                q = (npix >> QUANT_SHIFT).astype(np.uint8)          # quantize
                block_idx_frame = lut[q[:,:,0], q[:,:,1], q[:,:,2]] # (H, W) uint8

                # --- Build fill commands for changed rows only ---
                all_commands = []
                current_rows = {}
                for row in range(SCREEN_H):
                    row_indices = tuple(block_idx_frame[row].tolist())
                    current_rows[row] = row_indices
                    if prev_rows.get(row) != row_indices:
                        all_commands.extend(row_to_fill_commands(row, row_indices, col_xz, row_y))

                prev_rows = current_rows

                # --- Send in parallel ---
                futures = [executor.submit(pool.send, cmd) for cmd in all_commands]
                for f in futures:
                    f.result()

                elapsed = time.time() - t0
                frame_n += 1
                print(f"Frame {frame_n}: {len(all_commands)} cmds | {elapsed:.3f}s | {1/elapsed:.1f} fps")

                wait = frame_gap - elapsed
                if wait > 0:
                    time.sleep(wait)

    pool.close()


if __name__ == "__main__":
    main()
