#!/usr/bin/env python3
"""Inject a keypress into the running PS2Recomp game window (XTEST).

Usage:  scripts/06_sendkey.py <keysym> <hold_seconds>
Example: scripts/06_sendkey.py Down 12      # hold D-pad DOWN for 12s
         scripts/06_sendkey.py x 6          # hold CROSS for 6s
         scripts/06_sendkey.py F1 0.15      # tap F1 (toggles the ImGui debug overlay)

WHY THIS EXISTS: `xdotool` / `wmctrl` are NOT installed on this box. This uses python-xlib +
the XTEST extension (both present) to focus the game window and synthesise real key events,
which is what raylib's IsKeyDown() -- and therefore the pad backend -- actually reads.

KEY MAP (ps2xRuntime/src/lib/ps2_pad.cpp): UP/W, DOWN/S, LEFT/A, RIGHT/D, X or SPACE = CROSS,
C or ESCAPE = CIRCLE, Z/KP_0, V/KP_1, Q, E, SHIFTs, ENTER = START, TAB = SELECT.

GOTCHAS:
  - FOCUS-SENSITIVE: if the desktop focus moves (you click elsewhere), the key never reaches
    raylib. "No [pad:sample] btns line in the log" means the key never arrived, not a game bug.
  - The game may run at ~1 fps (software rasterizer). The pad is polled once per frame, so a
    short tap can be missed entirely -- HOLD keys for several seconds.
  - The runner draws an ImGui "Runtime Debugger" overlay (visible BY DEFAULT) on top of the
    game. Tap F1 before screenshotting or it covers the framebuffer.
"""
import sys
import os
import time

from Xlib import X, XK, display, protocol
from Xlib.ext import xtest

# The runner's window title: "PS2-Recomp | <ELF>" by default, or the game's own name once it registers
# one (ps2_launcher.h -- rotk: "The Lord of the Rings: The Return of the King"). Set PS2X_WIN_TITLE to
# match another title; both are tried.
WIN_TITLE_MATCHES = [t for t in (os.environ.get("PS2X_WIN_TITLE"), "PS2-Recomp") if t]


def find_win(win):
    try:
        name = win.get_wm_name()
    except Exception:
        name = None
    if name and any(t in name for t in WIN_TITLE_MATCHES):
        return win
    try:
        for child in win.query_tree().children:
            found = find_win(child)
            if found:
                return found
    except Exception:
        pass
    return None


def activate(d, win):
    """Focus the game window and VERIFY it took.

    set_input_focus() alone loses to the window manager's focus-stealing prevention: the WM
    hands focus straight back to whatever the user is typing in, XTEST keys go there, and the
    game reads keyDown=0 forever. That misreads as "input doesn't work" -- it has produced
    several false negatives. So ask the WM properly, via the EWMH _NET_ACTIVE_WINDOW client
    message (what `wmctrl -a` sends), and only then force the focus ourselves. Then CHECK:
    if the window still isn't focused, say so loudly rather than silently injecting into
    someone else's window.
    """
    root = d.screen().root
    try:
        net_active = d.intern_atom("_NET_ACTIVE_WINDOW")
        ev = protocol.event.ClientMessage(
            window=win, client_type=net_active,
            data=(32, [2, X.CurrentTime, 0, 0, 0]),   # source indication 2 = pager
        )
        mask = X.SubstructureRedirectMask | X.SubstructureNotifyMask
        root.send_event(ev, event_mask=mask)
        d.sync()
        time.sleep(0.3)
    except Exception as exc:                     # no EWMH WM -> fall through to the raw path
        print("note: _NET_ACTIVE_WINDOW failed (%s)" % exc)

    win.set_input_focus(X.RevertToParent, X.CurrentTime)
    win.configure(stack_mode=X.Above)
    # Many WMs here are focus-follows-mouse: XSetInputFocus is instantly reverted to whatever
    # window the pointer sits over, so XTEST keys land elsewhere and the game reads keyDown=0
    # (confirmed: focus verified on the game window, yet the key never arrived). Warp the
    # pointer into the game window so pointer-based focus AGREES with the focus we just set.
    try:
        geo = win.get_geometry()
        win.warp_pointer(geo.width // 2, geo.height // 2)
    except Exception as exc:
        print("note: warp_pointer failed (%s)" % exc)
    d.sync()
    time.sleep(0.5)

    focused = d.get_input_focus().focus
    fid = getattr(focused, "id", 0)
    if fid != win.id:
        # Not fatal: the focused window may be a child of ours. Warn with both ids so a
        # "the key never arrived" result is never mistaken for a game bug.
        print("WARNING: focus is 0x%x, not the game window 0x%x -- keys may not arrive"
              % (fid, win.id))
    else:
        print("focus confirmed on 0x%x" % win.id)


def main():
    keyname = sys.argv[1] if len(sys.argv) > 1 else "Down"
    hold = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0

    d = display.Display()
    win = find_win(d.screen().root)
    if not win:
        print("ERROR: no window titled any of %s (is the runner running? set PS2X_WIN_TITLE)" % WIN_TITLE_MATCHES)
        return 1
    print("window id=0x%x name=%r" % (win.id, win.get_wm_name()))

    activate(d, win)

    keysym = XK.string_to_keysym(keyname)
    keycode = d.keysym_to_keycode(keysym)
    if not keycode:
        print("ERROR: no keycode for keysym %r" % keyname)
        return 1
    print("key=%s keycode=%d hold=%.2fs" % (keyname, keycode, hold))

    xtest.fake_input(d, X.KeyPress, keycode)
    d.sync()
    time.sleep(hold)
    xtest.fake_input(d, X.KeyRelease, keycode)
    d.sync()
    print("sent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
