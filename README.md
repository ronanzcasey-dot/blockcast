# blockcast

Stream your screen into Minecraft in real time — one block per pixel.

https://github.com/user-attachments/assets/placeholder

## How it works

A Python script captures your screen (or a specific window), maps each pixel to the closest Minecraft block color using a precomputed LAB color lookup table, then sends `/fill` commands to your server via RCON. A Skript command sets the canvas location in-world.

- Block colors are extracted directly from the 1.21.11 game textures for accuracy
- Color matching uses CIE L\*a\*b\* distance (perceptually accurate, not just RGB)
- Delta updates — only changed rows are sent each frame
- RLE encoding — consecutive same-color pixels in a row become a single `/fill` command
- 200 parallel RCON connections for maximum throughput

## Requirements

- Minecraft Paper server 1.21.11
- [Skript](https://github.com/SkriptLang/Skript) plugin
- Python 3.x (macOS system Python recommended — `/usr/bin/python3`)
- Python packages: `mss`, `Pillow`, `numpy`, `mcrcon`, `pyobjc-framework-Quartz`

## Setup

### 1. Server config

In `server.properties`, make sure RCON is enabled:

```
enable-rcon=true
rcon.port=25575
rcon.password=yourpassword
```

Update the password in `screen_capture.py`:

```python
RCON_PASSWORD = "yourpassword"
```

### 2. Install Python dependencies

```bash
pip3 install mss Pillow numpy mcrcon pyobjc-framework-Quartz
```

### 3. Install the Skript

Copy `screen_display.sk` into your server's `plugins/Skript/scripts/` folder, then run:

```
/sk reload screen_display
```

### 4. Place the canvas

Stand in-game where you want the bottom-left corner of the display, then run:

```
/screen
```

### 5. Run the Python script

```bash
python3 screen_capture.py
```

At startup you'll be asked which window to capture (or press 0 for full screen):

```
Available windows (0 = full screen):
  0: Full screen
  1: Safari — YouTube
  2: ...

Enter number:
```

## Configuration

At the top of `screen_capture.py`:

| Variable | Default | Description |
|---|---|---|
| `SCREEN_W` | `160` | Canvas width in blocks |
| `SCREEN_H` | `90` | Canvas height in blocks |
| `TARGET_FPS` | `30` | Max frames per second |
| `NUM_WORKERS` | `200` | Parallel RCON connections |
| `PLAYER` | `"rizzguy"` | Your in-game username |

Higher resolution = more blocks = slower updates. 160×90 is a good balance.

## Tips

- For best performance, keep the server on the same machine
- To capture a YouTube video, press `F` inside the browser to go fullscreen within the window — don't use macOS native fullscreen (it causes Space-switching)
- The canvas is built perpendicular to the direction you're facing when you run `/screen`
- Run `/screen` again to move the canvas to a new location
