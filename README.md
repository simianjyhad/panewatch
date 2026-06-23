
markdown# PaneWatch

A lightweight, terminal-based log viewer that lets you watch multiple logs simultaneously in split panes — with colour coding, search, continuous recording, and live reload. No dependencies beyond Python 3 and standard system tools.

This started as a personal tool for everyday troubleshooting, but it ended up proving its worth on a real bug: an intermittent compositor-level input freeze that was otherwise nearly impossible to catch. Continuous background recording across multiple log sources (kernel, raw input events, and compositor logs simultaneously) turned an unreproducible "this feels broken but I can't prove it" problem into hard, timestamped evidence — including catching the bug live and tracing it to a known upstream issue. That use case shaped a lot of the recording and safety features below.

---

## Requirements

- Python 3.6+
- A terminal emulator (kitty, gnome-terminal, xfce4-terminal, etc.)
- For clipboard support (optional but recommended):
  - **Wayland:** `sudo apt install wl-clipboard`
  - **X11:** `sudo apt install xclip`

---

## Installation

1. Copy `panewatch.py` to your home directory or anywhere on your PATH:
```bash
   cp panewatch.py ~/panewatch.py
```

2. Copy the example config to your home directory:
```bash
   cp panewatch.conf.example ~/.panewatch.conf
```

3. Edit `~/.panewatch.conf` to set up the logs you want to watch (see Configuration below).

4. Run it:
```bash
   python3 ~/panewatch.py
```

---

## Configuration (`~/.panewatch.conf`)

The config file controls which logs are shown, colours, and keywords. Lines starting with `#` are comments and are ignored. Inline comments are also supported:
allboot=journalctl -f -b    # this comment is fine and will be ignored

The parser is strict about what it accepts to prevent accidental misconfiguration:
- Labels must be short (max 20 chars), alphanumeric, hyphens and underscores only
- Commands must start with a known executable (see list below)
- Color values must be a known color name
- Any line that doesn't match a known pattern is silently ignored

**Known commands:** `journalctl`, `tail`, `cat`, `grep`, `echo`, `dmesg`, `kubectl`, `docker`, `ssh`, `libinput`, `evtest`

To use a command not in this list, add it to the `KNOWN_COMMANDS` set near the top of `panewatch.py`.

### Adding log panes
label=command

Each line defines one pane. The label is shown in the pane header. The command is anything that produces output to stdout — `journalctl -f`, `tail -f`, `evtest`, or any custom script.
kernel=journalctl -f -k

journal=journalctl -f

auth=journalctl -f _COMM=sudo

syslog=tail -f /var/log/syslog

Up to 4 panes are recommended. Layout is automatic:

| Panes | Layout |
|-------|--------|
| 1 | Full screen |
| 2 | Side by side (wide terminal) or stacked (tall terminal) |
| 3 | One full-width top, two side by side bottom |
| 4 | 2×2 grid |

### Example configurations

**Basic system monitoring** — a good starting point for general use:
kernel=journalctl -f -k

journal=journalctl -f

auth=tail -f /var/log/auth.log

syslog=tail -f /var/log/syslog

**Watching a specific service** — useful when you're working on or debugging one thing:
nginx=journalctl -f -u nginx

nginxerr=tail -f /var/log/nginx/error.log

**Input and compositor debugging** — the setup PaneWatch was originally built for. Catches intermittent input freezes by recording the kernel input layer, raw device events, and compositor logs simultaneously:
compositor=journalctl -f _COMM=cosmic-comp

kernel=journalctl -f -k

keyboard=evtest /dev/input/eventN

autorecord=true
Replace `eventN` with your device path (`ls /dev/input/by-id/` to find it). `evtest` requires root. With `autorecord=true` every pane records to disk from the moment PaneWatch launches — no need to remember to press `l` before the next freeze hits.

### Colour overrides

Default colours can be overridden. Available colours: `white`, `yellow`, `red`, `magenta`, `green`, `cyan`, `blue`.
color.normal=white

color.warning=yellow

color.error=red

color.denied=magenta

color.highlight=cyan

### Keyword overrides

Lines are colour-coded based on keywords. You can override the default keyword lists:
keywords.warning=warning,warn

keywords.error=error,fail,critical,failed

keywords.denied=denied,permission

### Auto-record on launch

By default, recording (see below) only starts when you press `l`. If you'd rather every pane start recording the instant PaneWatch launches — useful if you're trying to catch something intermittent and don't want to risk forgetting — add this anywhere in the config:
autorecord=true

With this set, every pane (including any added later via live reload) starts writing to disk immediately, no keypress needed.

---

## Key Bindings

| Key | Action |
|-----|--------|
| `q` | Quit (also handles Ctrl+C / Ctrl+\\ / external kill gracefully — recordings are closed cleanly either way) |
| `Tab` | Switch active pane (highlighted header shows which is active) |
| `z` | Maximize active pane to full screen / restore grid view |
| `s` | Toggle scroll mode: AUTO (follows live output) / LOCK (frozen) |
| `Shift+T` | Snap active pane back to tail (most recent entry) — useful after scrolling up to review old logs |
| `↑` / `↓` | Scroll up/down in LOCK mode |
| `←` / `→` | Rotate pane order clockwise/counter-clockwise — cycles any log into the top/primary position |
| `m` | Toggle mouse mode: NAV (click panes / scroll within the app) / SEL (passes mouse through to the terminal for native click-drag text selection) |
| `e` | Export last 30 lines of active pane to `~/panewatch_exports/` and copy to clipboard |
| `l` | Toggle **continuous recording** of active pane to `~/panewatch_exports/` — every new line is written to disk live, not just a 30-line snapshot. Header shows `REC` while active |
| `/` | Enter search — type term, Enter to confirm, Esc to clear |
| `f` | Toggle filter mode (requires an active search term) — hides all non-matching lines instead of just highlighting matches |
| `Esc` | Clear active search |
| `p` | Pause all panes (screen freezes — useful for reading fast logs or selecting text) |
| `r` | Live reload config — add/remove panes without quitting, existing buffers preserved |

---

## Features

### Colour coding
Lines are automatically coloured based on keywords:
- **Red** — errors (`error`, `fail`, `critical`, `failed`)
- **Yellow** — warnings (`warning`, `warn`)
- **Magenta** — denied/permission messages (`denied`, `permission`)
- **Cyan + bold** — search matches

All colours and keywords are configurable in `~/.panewatch.conf`.

### Search / highlight
Press `/` to open the search bar. Type a term and press Enter. All matching lines in the active pane are highlighted cyan and bold. The search term is shown in the pane header. Each pane has its own independent search. Press Esc to clear.

With an active search, press `f` to switch from highlighting to **filtering** — only matching lines are shown, everything else is hidden. Useful for cutting a noisy pane down to just what you care about. Press `f` again to go back to highlight mode.

### Export
Press `e` to export the last 30 lines of the active pane to a timestamped file in `~/panewatch_exports/`. The content is also copied to the clipboard automatically (requires `wl-clipboard` on Wayland or `xclip` on X11). A confirmation message is shown briefly in the status bar.

### Continuous recording
Press `l` to start recording the active pane. Unlike `e` (a one-time 30-line snapshot), this writes every new line to a timestamped file in `~/panewatch_exports/` live, for as long as recording is on — essential for catching intermittent issues, since you don't need to react fast enough to export before the relevant context scrolls past. Press `l` again to stop. The pane header shows `REC` while active. Set `autorecord=true` in the config to have every pane start recording automatically on launch instead of needing a manual toggle.

**Knowing what's safe to delete:** `~/panewatch_exports/ACTIVE_RECORDINGS.txt` always lists exactly which files are currently being written to. Check it before cleaning up old exports — on Linux, deleting a file that's still open doesn't error or warn you, it just silently orphans the data the moment the program closes that handle.

### Snap to tail
Press `Shift+T` to instantly jump the active pane back to the most recent log entry, regardless of how far you have scrolled up. A brief confirmation is shown in the status bar. Scroll mode is also disabled if it was on. Each pane has independent scroll position so you can review history in one pane while others continue following live output.

### Maximize
Press `z` to expand the active pane to fill the entire screen, hiding the others — handy when you want to read or copy from a single pane without dividers or neighboring panes in the way. Press `z` again to return to the grid.

### Graceful shutdown
`q`, Ctrl+C (SIGINT), Ctrl+\\ (SIGQUIT), and SIGTERM are all handled the same way: any active recordings are flushed and closed properly before exit. No lost recording footers or surprise core dumps from a stray keypress.

### Pause mode
Press `p` to freeze the screen. All panes stop redrawing, making it easy to read fast-moving logs or select text. Logs continue to be collected (and recorded, if active) in the background. Press any key to resume.

### Live reload
Edit `~/.panewatch.conf` in another terminal while PaneWatch is running, then press `r` to reload. New panes are started immediately. Existing panes that are still in the config keep their buffers intact — no data is lost. Removed panes are stopped cleanly.

### Line wrapping
Long lines are wrapped to fit the pane width. A blank line is inserted after each log entry for readability.

---

## Tips

- **Backup before editing:**
```bash
  cp ~/panewatch.py ~/panewatch.py.$(date +%Y%m%d)
```

- **On Pop!_OS / Ubuntu**, most logs go through systemd so `journalctl` sources are more reliable than `/var/log/` files.

- **To watch a specific service:**
nginx=journalctl -f -u nginx

ssh=journalctl -f -u ssh

- **To watch only errors and above:**
errors=journalctl -f -p err

- **To capture raw input events** (useful for diagnosing input/compositor issues):
keyboard=evtest /dev/input/eventN
  Replace `eventN` with your device path. `evtest` must be run as root.

- **Clipboard not working?** Check that `wl-copy` is installed (`which wl-copy`) and that `WAYLAND_DISPLAY` is set (`echo $WAYLAND_DISPLAY`).

---

## Files

| File | Purpose |
|------|---------|
| `panewatch.py` | Main script |
| `panewatch.conf.example` | Example config — copy to `~/.panewatch.conf` |
| `README.md` | This file |