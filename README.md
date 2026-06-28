# blockcast

> **macOS only** — uses Quartz for window capture.

Stream your screen into Minecraft in real time using colored blocks.

Your screen is downscaled to a configurable resolution (default 160×90) and rendered as a wall of blocks in-world, updated continuously. Each block represents one pixel of the downscaled image, matched to the closest available block color.

## How it works

- **Python** captures your screen (or a specific window), resizes it, and maps each pixel to the nearest Minecraft block using a precomputed color lookup table
- **Skript** handles the `/screen` command to set the canvas location in-world
- Commands are sent to the server via **RCON** using up to 200 parallel connections
- Block colors are extracted directly from the 1.21.11 game textures (not hardcoded guesses)
- Color matching uses **CIE L\*a\*b\* distance** — perceptually accurate, not just RGB Euclidean
- Only changed rows are sent each frame (delta updates)
- Consecutive same-color pixels in a row are batched into a single `/fill` command (RLE)

## Requirements

- Paper Minecraft server 1.21.11
- [Skript](https://github.com/SkriptLang/Skript) plugin
- Python 3 (macOS — uses Quartz for window capture)
- Python packages:

```bash
pip3 install mss Pillow numpy mcrcon pyobjc-framework-Quartz
```

## Setup

### 1. Enable RCON in `server.properties`

```properties
enable-rcon=true
rcon.port=25575
rcon.password=yourpassword
```

Restart the server after changing this.

### 2. Configure the script

At the top of `screen_capture.py`, set your credentials:

```python
RCON_PASSWORD = "yourpassword"  # matches rcon.password above
PLAYER        = "YourUsername"  # your Minecraft username
```

### 3. Install the Skript

Copy `screen_display.sk` into your server's `plugins/Skript/scripts/` folder, then in-game run:

```
/sk reload screen_display
```

### 4. Set the canvas location

Stand in-game where you want the **bottom-left corner** of the display to be, facing the direction you want it to face, then run:

```
/screen
```

The canvas will be built perpendicular to the direction you're facing.

### 5. Run the script

```bash
python3 screen_capture.py
```

You'll be prompted to pick a window to capture, or press 0 for full screen:

```
Available windows (0 = full screen):
  0: Full screen
  1: Safari — YouTube
  2: Google Chrome — ...

Enter number:
```

The chosen window is captured directly by window ID — it does not need to be in focus or visible. Minecraft can be on top and it will still capture the correct window.

> **Note:** macOS native fullscreen (separate Space) causes Space-switching when captured. Instead, use the browser's built-in fullscreen (press `F` on YouTube) which stays within the window.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `SCREEN_W` | `160` | Canvas width in blocks |
| `SCREEN_H` | `90` | Canvas height in blocks |
| `TARGET_FPS` | `30` | Max frames per second |
| `NUM_WORKERS` | `200` | Parallel RCON connections |
| `PLAYER` | — | Your Minecraft username |
| `RCON_PASSWORD` | — | Your RCON password |

Higher resolution means more blocks and slower updates. 160×90 runs well on a local server.

## Block palette

Uses 49 solid, non-falling blocks including all 16 concrete colors, all terracotta variants, and misc blocks (obsidian, quartz, prismarine, etc.). No sand, gravel, or concrete powder.
