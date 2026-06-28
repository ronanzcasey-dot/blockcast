#!/usr/bin/env python3
"""
blockcast — stream your screen into Minecraft in real time using colored blocks.
Works on macOS, Windows, and Linux.

Stand where you want the canvas, run /screen in-game, then run this script.
"""

import ctypes
import platform
import subprocess
import time
import sys
import signal
import threading
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from mcrcon import MCRcon
import mss
from PIL import Image

RCON_HOST     = "127.0.0.1"
RCON_PORT     = 25575
RCON_PASSWORD = "yourpassword"   # set this to your rcon.password in server.properties
PLAYER        = "YourUsername"   # your Minecraft username

SCREEN_W    = 160
SCREEN_H    = 90
TARGET_FPS  = 30
NUM_WORKERS = 200

QUANT_BITS  = 5
QUANT_SHIFT = 8 - QUANT_BITS
LUT_SIZE    = 1 << QUANT_BITS

OS = platform.system()  # 'Darwin', 'Windows', 'Linux'

# ── Platform imports ──────────────────────────────────────────────────────────

if OS == "Darwin":
    from Quartz import (CGWindowListCopyWindowInfo, kCGNullWindowID,
                        CGWindowListCreateImage, CGRectInfinite,
                        CGImageGetWidth, CGImageGetHeight,
                        CGColorSpaceCreateDeviceRGB, CGBitmapContextCreate,
                        CGContextDrawImage, CGRectMake,
                        kCGWindowListOptionIncludingWindow,
                        kCGWindowImageBoundsIgnoreFraming)

elif OS == "Windows":
    try:
        import win32gui, win32ui, win32con
        from ctypes import windll
    except ImportError:
        print("Missing dependency. Run:  pip install pywin32")
        sys.exit(1)

# Linux uses wmctrl (apt install wmctrl) — checked at runtime in pick_window()

# ── Block palette (colors from 1.21.11 textures, no falling blocks) ───────────

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


# ── Color matching ────────────────────────────────────────────────────────────

def rgb_to_lab(rgb):
    """Convert Nx3 float32 array (0-255) to CIE L*a*b* (vectorized)."""
    c = rgb / 255.0
    mask = c > 0.04045
    c[mask]  = ((c[mask] + 0.055) / 1.055) ** 2.4
    c[~mask] = c[~mask] / 12.92
    M = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]], dtype=np.float32)
    xyz = c @ M.T
    xyz /= np.array([0.95047, 1.00000, 1.08883], dtype=np.float32)
    eps = 0.008856
    kap = 903.3
    mask2 = xyz > eps
    f = np.where(mask2, np.cbrt(xyz), (kap * xyz + 16.0) / 116.0)
    L = 116.0 * f[:, 1] - 16.0
    a = 500.0 * (f[:, 0] - f[:, 1])
    b = 200.0 * (f[:, 1] - f[:, 2])
    return np.stack([L, a, b], axis=-1)


def build_lut():
    """Precompute 32x32x32 LUT: quantized RGB -> palette index, using LAB distance."""
    print("Building color LUT...", end=" ", flush=True)
    levels = np.arange(LUT_SIZE, dtype=np.float32) * (1 << QUANT_SHIFT) + (1 << (QUANT_SHIFT - 1))
    rr, gg, bb = np.meshgrid(levels, levels, levels, indexing='ij')
    pixels_rgb  = np.stack([rr, gg, bb], axis=-1).reshape(-1, 3)
    pixels_lab  = rgb_to_lab(pixels_rgb.copy())
    palette_lab = rgb_to_lab(_PAL_NP.copy())
    diffs = pixels_lab[:, None, :] - palette_lab[None, :, :]
    dists = np.sum(diffs ** 2, axis=-1)
    lut   = np.argmin(dists, axis=-1).reshape(LUT_SIZE, LUT_SIZE, LUT_SIZE).astype(np.uint8)
    print("done.")
    return lut


# ── Window picker ─────────────────────────────────────────────────────────────

def pick_window():
    """List open windows and let the user pick one. Returns (window_handle, label)."""

    if OS == "Darwin":
        from Quartz import CGWindowListCopyWindowInfo, kCGNullWindowID
        wins = CGWindowListCopyWindowInfo(0, kCGNullWindowID)
        entries = []
        for w in wins:
            name   = w.get("kCGWindowOwnerName", "")
            title  = w.get("kCGWindowName", "")
            bounds = w.get("kCGWindowBounds", {})
            wid    = w.get("kCGWindowNumber", 0)
            if name and bounds.get("Width", 0) >= 100:
                entries.append((f"{name}" + (f" — {title}" if title else ""),
                                wid, int(bounds["Width"]), int(bounds["Height"]), None))

    elif OS == "Windows":
        entries = []
        def _cb(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                rect  = win32gui.GetWindowRect(hwnd)
                w, h  = rect[2] - rect[0], rect[3] - rect[1]
                if title and w >= 100:
                    entries.append((title, hwnd, w, h, rect))
        win32gui.EnumWindows(_cb, None)

    elif OS == "Linux":
        entries = []
        try:
            out = subprocess.check_output(["wmctrl", "-l", "-G"], text=True)
            for line in out.strip().splitlines():
                parts = line.split(None, 8)
                if len(parts) < 9:
                    continue
                wid = int(parts[0], 16)
                x, y, w, h = int(parts[2]), int(parts[3]), int(parts[4]), int(parts[5])
                title = parts[8]
                if w >= 100:
                    entries.append((title, wid, w, h, (x, y, w, h)))
        except FileNotFoundError:
            print("wmctrl not found. Install it:  sudo apt install wmctrl")

    else:
        entries = []

    # Deduplicate by label
    seen, deduped = set(), []
    for entry in entries:
        label = entry[0]
        if label not in seen:
            seen.add(label)
            deduped.append(entry)

    print(f"\nAvailable windows (0 = full screen)  [{OS}]:")
    print("  0: Full screen")
    for i, (label, wid, w, h, _) in enumerate(deduped, 1):
        print(f"  {i}: {label}  [{w}x{h}]")

    try:
        choice = int(input("\nEnter number: ").strip())
    except (ValueError, EOFError):
        choice = 0

    if choice == 0 or choice > len(deduped):
        print("Using full screen.")
        return None, None

    label, wid, w, h, extra = deduped[choice - 1]
    print(f"Capturing: {label}")
    return wid, extra  # extra = rect/bounds for Linux/Windows


# ── Window capture ────────────────────────────────────────────────────────────

def grab_window_macos(window_id):
    """Capture a window by ID — works even when covered by other windows."""
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


def grab_window_windows(hwnd):
    """Capture a window by HWND using PrintWindow — works even when covered."""
    rect = win32gui.GetWindowRect(hwnd)
    w, h = rect[2] - rect[0], rect[3] - rect[1]
    if w <= 0 or h <= 0:
        return None
    hwnd_dc  = win32gui.GetWindowDC(hwnd)
    mfc_dc   = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc  = mfc_dc.CreateCompatibleDC()
    bitmap   = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(mfc_dc, w, h)
    save_dc.SelectObject(bitmap)
    windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 3)
    bmp_str = bitmap.GetBitmapBits(True)
    arr = np.frombuffer(bmp_str, dtype=np.uint8).reshape((h, w, 4))
    win32gui.DeleteObject(bitmap.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)
    return arr[:, :, 2::-1].copy()  # BGRA -> RGB


def grab_region_mss(region):
    """Capture a screen region using mss (Linux / fallback)."""
    with mss.MSS() as sct:
        raw = sct.grab(region)
        arr = np.frombuffer(raw.raw, dtype=np.uint8).reshape((raw.height, raw.width, 4))
        return arr[:, :, 2::-1].copy()


def grab_fullscreen():
    with mss.MSS() as sct:
        mon = sct.monitors[1]
        raw = sct.grab(mon)
        arr = np.frombuffer(raw.raw, dtype=np.uint8).reshape((raw.height, raw.width, 4))
        return arr[:, :, 2::-1].copy()


# ── RCON ──────────────────────────────────────────────────────────────────────

class RconPool:
    def __init__(self, size):
        self._size     = size
        self._conns    = []
        self._locks    = []
        self._idx_lock = threading.Lock()
        self._idx      = 0
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


# ── Canvas ────────────────────────────────────────────────────────────────────

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
    if   yaw < 45  or yaw >= 315: return  center, 0
    elif yaw < 135:                return  0, -center
    elif yaw < 225:                return -center, 0
    else:                          return  0,  center


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


# ── Main ──────────────────────────────────────────────────────────────────────

running = True

def handle_exit(sig, frame):
    global running
    running = False
    print("\nStopping...")

signal.signal(signal.SIGINT, handle_exit)


def main():
    lut = build_lut()

    window_id, window_extra = pick_window()

    print(f"Resolution: {SCREEN_W}x{SCREEN_H} | Workers: {NUM_WORKERS} | FPS cap: {TARGET_FPS} | OS: {OS}")
    pool = RconPool(NUM_WORKERS)

    ox, oy, oz, yaw = get_canvas_origin(pool.get_one())
    col_xz, row_y   = precompute_coords(ox, oy, oz, yaw, SCREEN_W, SCREEN_H)

    prev_rows = {}
    frame_gap = 1.0 / TARGET_FPS
    frame_n   = 0

    with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
        while running:
            t0 = time.time()

            # --- Capture frame ---
            try:
                if window_id is None:
                    arr = grab_fullscreen()
                elif OS == "Darwin":
                    arr = grab_window_macos(window_id)
                    if arr is None:
                        print("Window lost, switching to full screen.")
                        window_id = None
                        arr = grab_fullscreen()
                elif OS == "Windows":
                    arr = grab_window_windows(window_id)
                    if arr is None:
                        print("Window lost, switching to full screen.")
                        window_id = None
                        arr = grab_fullscreen()
                elif OS == "Linux":
                    # window_extra is (x, y, w, h)
                    x, y, w, h = window_extra
                    arr = grab_region_mss({"left": x, "top": y, "width": w, "height": h})
                else:
                    arr = grab_fullscreen()
            except Exception as e:
                print(f"Capture error: {e} — falling back to full screen.")
                window_id = None
                arr = grab_fullscreen()

            img  = Image.fromarray(arr, "RGB").resize((SCREEN_W, SCREEN_H), Image.BILINEAR)
            npix = np.asarray(img, dtype=np.uint8)

            # --- Map pixels to blocks (vectorized) ---
            q = (npix >> QUANT_SHIFT).astype(np.uint8)
            block_idx_frame = lut[q[:,:,0], q[:,:,1], q[:,:,2]]

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
