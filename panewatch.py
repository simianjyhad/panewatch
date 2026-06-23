import curses
import subprocess
import threading
import os
import shlex
import signal
from collections import deque
from datetime import datetime

CONFIG_FILE = os.path.expanduser("~/.panewatch.conf")
BUFFER_SIZE = 1000

def copy_to_clipboard(text):
    """Copy text to clipboard using wl-copy (Wayland) or xclip (X11)."""
    if os.environ.get('WAYLAND_DISPLAY'):
        cmd = ['wl-copy']
    else:
        cmd = ['xclip', '-selection', 'clipboard']
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        proc.communicate(input=text.encode())
        return True
    except FileNotFoundError:
        return False

VALID_COLORS = {'white', 'yellow', 'red', 'magenta', 'green', 'cyan', 'blue'}
VALID_LABEL_CHARS = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-')
KNOWN_COMMANDS = {'journalctl', 'tail', 'cat', 'grep', 'echo', 'dmesg', 'kubectl', 'docker', 'ssh', 'libinput', 'evtest'}

def load_config():
    config = {
        'panes': [],
        'autorecord': False,
        'colors': {
            'normal': 'white',
            'warning': 'yellow',
            'error': 'red',
            'denied': 'magenta',
            'highlight': 'cyan'
        },
        'keywords': {
            'warning': ['warning', 'warn'],
            'error': ['error', 'fail', 'critical', 'failed'],
            'denied': ['denied', 'permission']
        }
    }
    try:
        with open(CONFIG_FILE) as f:
            for raw_line in f:
                # Strip inline comments first
                line = raw_line.split('#')[0].strip()
                if not line:
                    continue

                # Must contain = to be valid
                if '=' not in line:
                    continue

                key, _, value = line.partition('=')
                key = key.strip()
                value = value.strip()

                # global autorecord toggle
                if key == 'autorecord':
                    config['autorecord'] = value.strip().lower() in ('true', '1', 'yes', 'on')
                    continue

                # color.X override
                if key.startswith('color.'):
                    color_key = key.split('.', 1)[1]
                    if value in VALID_COLORS:
                        config['colors'][color_key] = value
                    continue

                # keywords.X override
                if key.startswith('keywords.'):
                    kw_key = key.split('.', 1)[1]
                    if kw_key and value:
                        config['keywords'][kw_key] = [k.strip() for k in value.split(',') if k.strip()]
                    continue

                # Pane definition — validate label and command
                label = key
                command = value

                # Label must start with a letter, be short, simple, no spaces
                if not label or not label[0].isalpha() or not all(c in VALID_LABEL_CHARS for c in label) or len(label) > 20:
                    continue

                # Command must be non-empty and start with a known executable
                if not command:
                    continue
                cmd_parts = command.split()
                base_cmd = os.path.basename(cmd_parts[0])
                if base_cmd not in KNOWN_COMMANDS:
                    continue

                config['panes'].append({
                    'label': label,
                    'command': command
                })

    except FileNotFoundError:
        config['panes'].append({
            'label': 'error',
            'command': 'echo config file not found'
        })
    return config

def sync_config_panes(active_commands):
    """
    Safely rewrite only the active pane lines in the conf file.
    - Lines that are valid pane definitions get commented out if not in active_commands
    - Everything else (comments, color/keyword overrides, blank lines) is untouched
    - Never modifies lines it doesn't recognise as pane definitions
    """
    try:
        with open(CONFIG_FILE, 'r') as f:
            raw_lines = f.readlines()

        new_lines = []
        for raw_line in raw_lines:
            # Detect if this is a full-line comment
            is_comment = raw_line.strip().startswith('#')
            # Strip inline comments to get the effective content
            stripped = raw_line.split('#')[0].strip()

            # Blank or pure comment with no pane content — pass through unchanged
            if '=' not in stripped:
                new_lines.append(raw_line)
                continue

            key, _, value = stripped.partition('=')
            key = key.strip()
            value = value.strip()

            # color/keyword overrides — pass through unchanged
            if key.startswith('color.') or key.startswith('keywords.'):
                new_lines.append(raw_line)
                continue

            # Validate as a pane definition
            label = key
            command = value
            if (not label or
                not label[0].isalpha() or
                not all(c in VALID_LABEL_CHARS for c in label) or
                len(label) > 20 or
                not command):
                new_lines.append(raw_line)
                continue

            cmd_parts = command.split()
            base_cmd = os.path.basename(cmd_parts[0])
            if base_cmd not in KNOWN_COMMANDS:
                new_lines.append(raw_line)
                continue

            # It's a valid pane definition (active or commented out)
            # Preserve any inline comment
            inline_comment = ''
            if not is_comment and '#' in raw_line:
                parts = raw_line.split('#', 1)
                if parts[0].strip():
                    inline_comment = '    #' + parts[1].rstrip('\n')

            if command in active_commands:
                new_lines.append(f"{label}={command}{inline_comment}\n")
            else:
                new_lines.append(f"#{label}={command}{inline_comment}\n")

        with open(CONFIG_FILE, 'w') as f:
            f.writelines(new_lines)
        return True
    except Exception:
        return False

COLOR_MAP = {
    'white': curses.COLOR_WHITE,
    'yellow': curses.COLOR_YELLOW,
    'red': curses.COLOR_RED,
    'magenta': curses.COLOR_MAGENTA,
    'green': curses.COLOR_GREEN,
    'cyan': curses.COLOR_CYAN,
    'blue': curses.COLOR_BLUE,
}

def init_colors(config):
    curses.start_color()
    curses.use_default_colors()
    pairs = {}
    pair_num = 1
    for name, color_name in config['colors'].items():
        fg = COLOR_MAP.get(color_name, curses.COLOR_WHITE)
        curses.init_pair(pair_num, fg, -1)
        pairs[name] = pair_num
        pair_num += 1
    return pairs

def get_line_color(line, keywords, pairs, search_term=''):
    # Search highlight takes priority
    if search_term and search_term.lower() in line.lower():
        return curses.color_pair(pairs.get('highlight', 0)) | curses.A_BOLD
    line_lower = line.lower()
    for category in ['error', 'denied', 'warning']:
        if category in keywords:
            for kw in keywords[category]:
                if kw in line_lower:
                    return curses.color_pair(pairs.get(category, 0))
    return curses.color_pair(pairs.get('normal', 0))

def wrap_line(line, width):
    """Split a line into chunks that fit within width."""
    if not line:
        return ['']
    if len(line) <= width:
        return [line]
    return [line[i:i+width] for i in range(0, len(line), width)]

def build_display_lines(buf, width, keywords, pairs, search_term='', filter_mode=False):
    """Build a flat list of (text, color) tuples with wrapping applied."""
    display = []
    for line in buf:
        # In filter mode, skip lines that don't match the search term
        if filter_mode and search_term and search_term.lower() not in line.lower():
            continue
        color = get_line_color(line, keywords, pairs, search_term)
        chunks = wrap_line(line, width)
        for chunk in chunks:
            display.append((chunk, color))
        display.append(('', curses.color_pair(pairs.get('normal', 0))))
    return display

def get_layout(num_panes, cols, lines):
    """Return layout mode based on pane count and terminal dimensions."""
    if num_panes == 1:
        return 'single'
    elif num_panes == 2:
        return 'horizontal' if cols >= lines * 2 else 'vertical'
    elif num_panes == 3:
        return 'triple'
    else:  # 4+
        return 'grid'

def draw_pane_content(stdscr, pane, i, active_pane, y_start, x_start, pane_lines, pane_width, keywords, pairs):
    """Draw a single pane's header and content at the given position."""
    label = pane['label']
    mode = 'AUTO' if pane['scroll'] else 'LOCK'
    search_term = pane.get('search_term', '')
    filter_mode = pane.get('filter_mode', False)
    if search_term:
        search_indicator = f' | FILTER:/{search_term}' if filter_mode else f' | /{search_term}'
    else:
        search_indicator = ''
    record_indicator = ' | REC' if pane.get('record_file') else ''
    header = f"[ {label} | {mode}{search_indicator}{record_indicator} ]"
    header = header + "-" * max(0, pane_width - len(header))
    header_attr = (curses.A_BOLD | curses.A_REVERSE) if i == active_pane else curses.A_NORMAL
    stdscr.addstr(y_start, x_start, header[:pane_width], header_attr)

    display = build_display_lines(
        list(pane['buffer']), pane_width, keywords, pairs, search_term, filter_mode
    )
    if pane['scroll']:
        visible = display[-max(pane_lines, 30):][-pane_lines:]
    else:
        offset = pane.get('offset', 0)
        visible = display[offset:offset + pane_lines]

    for j, (text, color) in enumerate(visible):
        if y_start + 1 + j < y_start + 1 + pane_lines:
            try:
                stdscr.addstr(y_start + 1 + j, x_start, text[:pane_width], color)
            except curses.error:
                pass

def draw_dividers(stdscr, orientation, lines, cols, usable, n):
    """Draw dividing lines between panes."""
    try:
        if orientation == 'vertical':
            pane_height = usable // n
            for i in range(1, n):
                y = i * pane_height - 1
                stdscr.addstr(y, 0, '─' * cols)
        elif orientation == 'horizontal':
            pane_width = cols // n
            for i in range(1, n):
                x = i * pane_width - 1
                for y in range(usable):
                    stdscr.addch(y, x, '│')
        elif orientation == 'triple':
            top_height = usable // 2
            half_width = cols // 2
            # Horizontal divider between top and bottom
            stdscr.addstr(top_height - 1, 0, '─' * cols)
            # Vertical divider between bottom two panes
            for y in range(top_height, usable):
                stdscr.addch(y, half_width - 1, '│')
            # Corner junction
            stdscr.addch(top_height - 1, half_width - 1, '┼')
        elif orientation == 'grid':
            half_height = usable // 2
            half_width = cols // 2
            # Horizontal divider
            stdscr.addstr(half_height - 1, 0, '─' * cols)
            # Vertical divider
            for y in range(usable):
                if y != half_height - 1:
                    stdscr.addch(y, half_width - 1, '│')
            # Centre junction
            stdscr.addch(half_height - 1, half_width - 1, '┼')
    except curses.error:
        pass

def draw_panes(stdscr, pane_data, orientation, lines, cols, keywords, pairs, active_pane=0):
    n = len(pane_data)
    # Reserve bottom line for status bar
    usable = lines - 1

    if orientation == 'single':
        draw_pane_content(stdscr, pane_data[0], 0, active_pane,
                         0, 0, usable - 1, cols, keywords, pairs)

    elif orientation == 'vertical':
        pane_height = usable // n
        draw_dividers(stdscr, orientation, lines, cols, usable, n)
        for i, pane in enumerate(pane_data):
            draw_pane_content(stdscr, pane, i, active_pane,
                             i * pane_height, 0, pane_height - 1, cols, keywords, pairs)

    elif orientation == 'horizontal':
        pane_width = (cols - (n - 1)) // n  # account for divider columns
        draw_dividers(stdscr, orientation, lines, cols, usable, n)
        for i, pane in enumerate(pane_data):
            draw_pane_content(stdscr, pane, i, active_pane,
                             0, i * (pane_width + 1), usable - 1, pane_width, keywords, pairs)

    elif orientation == 'triple':
        top_height = usable // 2
        bot_height = usable - top_height
        half_width = (cols - 1) // 2  # account for centre divider
        draw_dividers(stdscr, orientation, lines, cols, usable, n)
        draw_pane_content(stdscr, pane_data[0], 0, active_pane,
                         0, 0, top_height - 2, cols, keywords, pairs)
        draw_pane_content(stdscr, pane_data[1], 1, active_pane,
                         top_height, 0, bot_height - 1, half_width, keywords, pairs)
        draw_pane_content(stdscr, pane_data[2], 2, active_pane,
                         top_height, half_width + 1, bot_height - 1, half_width, keywords, pairs)

    elif orientation == 'grid':
        half_height = usable // 2
        half_width = (cols - 1) // 2  # account for centre divider
        draw_dividers(stdscr, orientation, lines, cols, usable, n)
        positions = [
            (0, 0), (0, half_width + 1),
            (half_height, 0), (half_height, half_width + 1)
        ]
        for i, pane in enumerate(pane_data[:4]):
            y, x = positions[i]
            draw_pane_content(stdscr, pane, i, active_pane,
                             y, x, half_height - 2, half_width, keywords, pairs)

EXPORT_KEEP = 10
WARNED_FLAG = os.path.expanduser('~/panewatch_exports/.cleanup_warned')

def cleanup_exports(export_dir, label):
    """Keep only the most recent EXPORT_KEEP exports for a given label. Returns True if cleanup ran."""
    pattern = os.path.join(export_dir, f'{label}_*.txt')
    import glob
    existing = sorted(glob.glob(pattern))
    if len(existing) > EXPORT_KEEP:
        to_remove = existing[:len(existing) - EXPORT_KEEP]
        for f in to_remove:
            try:
                os.remove(f)
            except OSError:
                pass
        return True
    return False

def show_cleanup_warning(stdscr, lines, cols):
    """Show a one-time centred warning overlay about export rotation."""
    msg_lines = [
        "  Export limit reached!  ",
        "",
        f"  Only the {EXPORT_KEEP} most recent exports  ",
        "  per log are kept. Older ones  ",
        "  will be deleted automatically.  ",
        "",
        "  This warning won't show again.  ",
        "",
        "  Press any key to continue...  ",
    ]
    box_w = max(len(l) for l in msg_lines) + 2
    box_h = len(msg_lines) + 2
    y_start = max(0, (lines - box_h) // 2)
    x_start = max(0, (cols - box_w) // 2)

    try:
        # Draw box
        stdscr.addstr(y_start, x_start, '┌' + '─' * (box_w - 2) + '┐')
        for i, line in enumerate(msg_lines):
            stdscr.addstr(y_start + 1 + i, x_start, '│' + line.ljust(box_w - 2) + '│')
        stdscr.addstr(y_start + box_h - 1, x_start, '└' + '─' * (box_w - 2) + '┘')
        stdscr.refresh()
    except curses.error:
        pass

    stdscr.nodelay(False)
    stdscr.getch()
    stdscr.nodelay(True)
    stdscr.timeout(500)

    # Mark as warned
    try:
        with open(WARNED_FLAG, 'w') as f:
            f.write('warned')
    except OSError:
        pass


def read_search_input(stdscr, lines, cols, current_term):
    """Show search input bar and read a search term. Returns new term or None on Escape."""
    curses.curs_set(1)
    stdscr.nodelay(False)
    term = current_term
    while True:
        prompt = f"/{term}|"
        try:
            stdscr.addstr(lines - 1, 0, f"Search: {prompt}"[:cols])
            stdscr.clrtoeol()
            stdscr.refresh()
        except curses.error:
            pass
        ch = stdscr.getch()
        if ch == 27:  # Escape — clear search
            curses.curs_set(0)
            stdscr.nodelay(True)
            return ''
        elif ch in (curses.KEY_ENTER, 10, 13):  # Enter — confirm
            curses.curs_set(0)
            stdscr.nodelay(True)
            return term
        elif ch in (curses.KEY_BACKSPACE, 127, 8, 263):
            term = term[:-1]
        elif 32 <= ch <= 126 and ch != 95:  # exclude underscore — used as cursor indicator
            term += chr(ch)

def write_active_recordings(pane_data):
    """Write the list of currently active recording file paths to a
    well-known file so the user can check what's safe to delete before
    cleaning up ~/panewatch_exports."""
    export_dir = os.path.expanduser('~/panewatch_exports')
    os.makedirs(export_dir, exist_ok=True)
    active_list_path = os.path.join(export_dir, 'ACTIVE_RECORDINGS.txt')
    try:
        active_paths = [p['record_path'] for p in pane_data if p.get('record_file')]
        with open(active_list_path, 'w') as f:
            stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            if active_paths:
                f.write(f"# Updated {stamp} — these files are LIVE, do not delete:\n")
                for path in active_paths:
                    f.write(path + '\n')
            else:
                f.write(f"# Updated {stamp} — no recordings currently active. Safe to clean up other files in this folder.\n")
    except Exception:
        pass


def open_recording(pane):
    """Open a new recording file for this pane, write the header, and
    store the open file handle + path on the pane dict. Returns the
    record_path on success, or None on failure."""
    label = pane['label']
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    export_dir = os.path.expanduser('~/panewatch_exports')
    os.makedirs(export_dir, exist_ok=True)
    record_path = os.path.join(export_dir, f'{label}_record_{timestamp}.txt')
    try:
        f = open(record_path, 'w')
        f.write(f"=== Recording started {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        f.write(f"=== Pane: {label} | Command: {pane['command']} ===\n")
        f.flush()
        pane['record_file'] = f
        pane['record_path'] = record_path
        return record_path
    except Exception:
        return None


def tail_log(command, pane, stop_event):
    try:
        # Commands containing a pipe (e.g. 'libinput debug-events | grep -i gesture')
        # need shell interpretation — shlex.split() alone tokenizes '|' as a literal
        # argument rather than a shell operator, which gets passed straight to the
        # first program and breaks it. Simple commands stay on the safer shlex.split
        # path (no shell involved, no shell-injection surface).
        if '|' in command:
            proc = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
        else:
            proc = subprocess.Popen(
                shlex.split(command),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
        while not stop_event.is_set():
            line = proc.stdout.readline()
            if line:
                clean = line.rstrip()
                pane['buffer'].append(clean)
                rec_file = pane.get('record_file')
                if rec_file:
                    try:
                        rec_file.write(clean + '\n')
                        rec_file.flush()
                    except Exception:
                        pass
        proc.terminate()
    except Exception as e:
        pane['buffer'].append(f"Error: {e}")

def get_pane_at(mouse_y, mouse_x, orientation, num_panes, lines, cols):
    """Return the pane index at the given mouse position, or -1 if none."""
    usable = lines - 1
    if orientation == 'single':
        return 0
    elif orientation == 'vertical':
        pane_height = usable // num_panes
        idx = mouse_y // pane_height
        return min(idx, num_panes - 1)
    elif orientation == 'horizontal':
        pane_width = (cols - (num_panes - 1)) // num_panes
        idx = mouse_x // (pane_width + 1)
        return min(idx, num_panes - 1)
    elif orientation == 'triple':
        top_height = usable // 2
        if mouse_y < top_height:
            return 0
        half_width = (cols - 1) // 2
        return 1 if mouse_x < half_width else 2
    elif orientation == 'grid':
        half_height = usable // 2
        half_width = (cols - 1) // 2
        row = 0 if mouse_y < half_height else 1
        col = 0 if mouse_x < half_width else 1
        idx = row * 2 + col
        return min(idx, num_panes - 1)
    return -1

def main(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(500)
    curses.mouseinterval(0)

    def enable_mouse():
        curses.mousemask(
            curses.BUTTON1_CLICKED |
            curses.BUTTON4_PRESSED |
            curses.BUTTON5_PRESSED
        )

    def disable_mouse():
        curses.mousemask(0)

    mouse_nav = True
    enable_mouse()

    config = load_config()
    pairs = init_colors(config)
    stop_event = threading.Event()

    def start_pane(pane_conf, existing_stop_event):
        """Start a new pane thread and return pane data dict."""
        buf = deque(maxlen=BUFFER_SIZE)
        pane = {
            'label': pane_conf['label'],
            'command': pane_conf['command'],
            'buffer': buf,
            'scroll': True,
            'offset': 0,
            'search_term': '',
            'filter_mode': False,
            'record_file': None,
            'record_path': None
        }
        t = threading.Thread(
            target=tail_log,
            args=(pane_conf['command'], pane, existing_stop_event),
            daemon=True
        )
        t.start()
        if config.get('autorecord'):
            open_recording(pane)
        return pane

    def close_recordings():
        """Flush and close any open recording files across all panes."""
        for p in pane_data:
            rf = p.get('record_file')
            if rf:
                try:
                    rf.write(f"=== Recording stopped (program exit) {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n")
                    rf.close()
                except Exception:
                    pass
                p['record_file'] = None
        write_active_recordings(pane_data)

    pane_data = [start_pane(p, stop_event) for p in config['panes']]
    write_active_recordings(pane_data)
    active_pane = 0
    paused = False
    maximized = False

    def handle_quit_signal(signum, frame):
        """Catch SIGQUIT (e.g. accidental Ctrl+\\) and SIGTERM — close
        recording files and exit cleanly instead of dumping core."""
        close_recordings()
        stop_event.set()
        raise SystemExit(0)

    signal.signal(signal.SIGQUIT, handle_quit_signal)
    signal.signal(signal.SIGTERM, handle_quit_signal)
    signal.signal(signal.SIGINT, handle_quit_signal)

    while True:
        if not paused:
            try:
                stdscr.clear()
                lines, cols = stdscr.getmaxyx()
                if maximized:
                    draw_panes(stdscr, [pane_data[active_pane]], 'single', lines, cols,
                              config['keywords'], pairs, 0)
                else:
                    orientation = get_layout(len(pane_data), cols, lines)
                    draw_panes(stdscr, pane_data, orientation, lines, cols,
                              config['keywords'], pairs, active_pane)
                # Status bar
                pane = pane_data[active_pane]
                export_msg = pane.get('export_msg', '')
                export_time = pane.get('export_msg_time', 0)
                if export_msg and (datetime.now().timestamp() - export_time) < 3:
                    stdscr.addstr(lines-1, 0, export_msg[:cols])
                else:
                    mouse_indicator = 'NAV' if mouse_nav else 'SEL'
                    full_bar = f"m:[{mouse_indicator}]  q:quit  s:lock  tab:switch  ↑↓:scroll  ←→:rotate  z:maximize  e:export  l:record  /:search  f:filter  p:pause  r:reload"
                    short_bar = f"m:[{mouse_indicator}]  q:quit  s:lock  tab  ↑↓  ←→  z:max  e:exp  l:rec  /:search  f:filt  p:pause  r:reload"
                    bar = full_bar if cols >= 114 else short_bar
                    stdscr.addstr(lines-1, 0, bar[:cols])
                stdscr.refresh()
            except curses.error:
                pass

        key = stdscr.getch()
        # If paused, any key resumes (except we still handle q to quit)
        if paused:
            if key == ord('q'):
                close_recordings()
                stop_event.set()
                break
            elif key != -1 and key != curses.KEY_MOUSE:
                paused = False
                stdscr.nodelay(True)
                stdscr.timeout(500)
            continue
            try:
                _, mx, my, _, bstate = curses.getmouse()
                lines, cols = stdscr.getmaxyx()
                orientation = get_layout(len(pane_data), cols, lines)
                if bstate & curses.BUTTON1_CLICKED:
                    # Click — switch active pane
                    idx = get_pane_at(my, mx, orientation, len(pane_data), lines, cols)
                    if 0 <= idx < len(pane_data):
                        active_pane = idx
                elif bstate & curses.BUTTON4_PRESSED:
                    # Scroll wheel up
                    pane = pane_data[active_pane]
                    if not pane['scroll']:
                        pane['offset'] = max(pane['offset'] - 1, 0)
                elif bstate & curses.BUTTON5_PRESSED:
                    # Scroll wheel down
                    pane = pane_data[active_pane]
                    if not pane['scroll']:
                        half_width = (cols - 1) // 2
                        pane_width = half_width if orientation in ('triple', 'grid') else cols
                        display = build_display_lines(
                            list(pane['buffer']), pane_width,
                            config['keywords'], pairs
                        )
                        pane_lines = lines // 2 if orientation in ('triple', 'grid') else lines - 2
                        max_offset = max(0, len(display) - pane_lines)
                        pane['offset'] = min(pane['offset'] + 1, max_offset)
            except curses.error:
                pass
        elif key == ord('p'):
            paused = True
            try:
                lines, cols = stdscr.getmaxyx()
                mouse_indicator = 'NAV' if mouse_nav else 'SEL'
                stdscr.addstr(lines-1, 0, f"m:[{mouse_indicator}]  -- PAUSED -- (any key to resume)"[:cols])
                stdscr.clrtoeol()
                stdscr.refresh()
            except curses.error:
                pass
            stdscr.nodelay(False)
        elif key == ord('q'):
            close_recordings()
            stop_event.set()
            break
        elif key == ord('m') and not paused:
            mouse_nav = not mouse_nav
            if mouse_nav:
                enable_mouse()
            else:
                disable_mouse()
        elif key == ord('r') and not paused:
            # Live reload — re-read config, preserve existing buffers
            new_config = load_config()
            existing = {p['command']: p for p in pane_data}
            config = new_config
            new_pane_data = []
            for pane_conf in new_config['panes']:
                if pane_conf['command'] in existing:
                    # Keep existing pane with its buffer intact
                    new_pane_data.append(existing[pane_conf['command']])
                else:
                    # New pane — start fresh
                    new_pane_data.append(start_pane(pane_conf, stop_event))
            # Stop threads for removed panes (they share stop_event so just drop refs)
            pane_data = new_pane_data
            write_active_recordings(pane_data)
            active_pane = min(active_pane, max(0, len(pane_data) - 1))
            # Write active panes back to conf to keep it in sync
            active_commands = {p['command'] for p in pane_data}
            sync_config_panes(active_commands)
            # Flash confirmation
            try:
                lines, cols = stdscr.getmaxyx()
                stdscr.addstr(lines-1, 0, f"Reloaded — {len(pane_data)} pane(s) active, conf synced"[:cols])
                stdscr.clrtoeol()
                stdscr.refresh()
            except curses.error:
                pass
        elif not paused:
            if key == ord('s'):
                pane_data[active_pane]['scroll'] = not pane_data[active_pane]['scroll']
                pane_data[active_pane]['offset'] = 0
            elif key == ord('\t'):
                active_pane = (active_pane + 1) % len(pane_data)
            elif key == ord('e'):
                pane = pane_data[active_pane]
                lines_to_export = list(pane['buffer'])[-30:]
                label = pane['label']
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                export_dir = os.path.expanduser('~/panewatch_exports')
                os.makedirs(export_dir, exist_ok=True)
                export_path = os.path.join(export_dir, f'{label}_{timestamp}.txt')
                export_text = '\n'.join(lines_to_export)
                with open(export_path, 'w') as f:
                    f.write(export_text)
                clipped = copy_to_clipboard(export_text)
                clip_note = ' + clipboard' if clipped else ' (install wl-copy or xclip for clipboard)'
                # Cleanup old exports — warn once if limit is hit
                cleaned = cleanup_exports(export_dir, label)
                if cleaned and not os.path.exists(WARNED_FLAG):
                    lines, cols = stdscr.getmaxyx()
                    show_cleanup_warning(stdscr, lines, cols)
                pane['export_msg'] = f"Exported to {export_path}{clip_note}"
                pane['export_msg_time'] = datetime.now().timestamp()
            elif key == ord('z'):
                maximized = not maximized
            elif key == ord('l'):
                pane = pane_data[active_pane]
                if pane.get('record_file'):
                    # Stop recording — close file cleanly
                    try:
                        pane['record_file'].write(
                            f"=== Recording stopped {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n"
                        )
                        pane['record_file'].close()
                    except Exception:
                        pass
                    stopped_path = pane.get('record_path')
                    pane['record_file'] = None
                    pane['record_path'] = None
                    pane['export_msg'] = f"Recording stopped: {stopped_path}"
                    pane['export_msg_time'] = datetime.now().timestamp()
                    write_active_recordings(pane_data)
                else:
                    # Start recording — open file, write header, start capturing new lines
                    record_path = open_recording(pane)
                    if record_path:
                        pane['export_msg'] = f"Recording to {record_path}"
                        pane['export_msg_time'] = datetime.now().timestamp()
                        write_active_recordings(pane_data)
                    else:
                        pane['export_msg'] = "Recording failed to start"
                        pane['export_msg_time'] = datetime.now().timestamp()
            elif key == ord('/'):
                lines, cols = stdscr.getmaxyx()
                current = pane_data[active_pane].get('search_term', '')
                new_term = read_search_input(stdscr, lines, cols, current)
                pane_data[active_pane]['search_term'] = new_term
                stdscr.timeout(500)
            elif key == 27:  # Escape — clear search and filter on active pane
                pane_data[active_pane]['search_term'] = ''
                pane_data[active_pane]['filter_mode'] = False
            elif key == ord('f'):
                pane = pane_data[active_pane]
                if pane.get('search_term'):
                    pane['filter_mode'] = not pane.get('filter_mode', False)
            elif key in (curses.KEY_LEFT, curses.KEY_RIGHT) and len(pane_data) > 1:
                # Rotate pane order clockwise (right) or counter-clockwise (left)
                if key == curses.KEY_RIGHT:
                    pane_data = [pane_data[-1]] + pane_data[:-1]
                else:
                    pane_data = pane_data[1:] + [pane_data[0]]
                # Keep active_pane index pointing at the same pane
                active_pane = min(active_pane, len(pane_data) - 1)
            elif key == ord('T'):
                # Shift+T — snap active pane back to tail (most recent entry)
                pane = pane_data[active_pane]
                pane['offset'] = 0
                pane['scroll'] = False
                try:
                    lines, cols = stdscr.getmaxyx()
                    stdscr.addstr(lines-1, 0, f"Snapped to tail: {pane['label']}"[:cols])
                    stdscr.clrtoeol()
                    stdscr.refresh()
                except curses.error:
                    pass
            elif key in (curses.KEY_UP, curses.KEY_DOWN):
                pane = pane_data[active_pane]
                if not pane['scroll']:
                    orientation = get_layout(len(pane_data), cols, lines)
                    # Determine pane dimensions based on layout
                    if orientation in ('single', 'vertical'):
                        pane_width = cols
                        pane_lines = (lines // max(len(pane_data), 1)) - 1
                    elif orientation == 'horizontal':
                        pane_width = cols // len(pane_data)
                        pane_lines = lines - 2
                    elif orientation == 'triple':
                        pane_width = cols if active_pane == 0 else cols // 2
                        pane_lines = (lines // 2) - 1
                    else:  # grid
                        pane_width = cols // 2
                        pane_lines = (lines // 2) - 1
                    display = build_display_lines(
                        list(pane['buffer']), pane_width,
                        config['keywords'], pairs
                    )
                    max_offset = max(0, len(display) - pane_lines)
                    if key == curses.KEY_UP:
                        pane['offset'] = min(pane['offset'] + 1, max_offset)
                    else:
                        pane['offset'] = max(pane['offset'] - 1, 0)

curses.wrapper(main)
