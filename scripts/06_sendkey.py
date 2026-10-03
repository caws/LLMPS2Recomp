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
import time

from Xlib import X, XK, display
from Xlib.ext import xtest

WIN_TITLE_MATCH = "PS2-Recomp"


def find_win(win):
    try:
        name = win.get_wm_name()
    except Exception:
        name = None
    if name and WIN_TITLE_MATCH in name:
        return win
    try:
        for child in win.query_tree().children:
            found = find_win(child)
            if found:
                return found
    except Exception:
        pass
    return None


def main():
    keyname = sys.argv[1] if len(sys.argv) > 1 else "Down"
    hold = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0

    d = display.Display()
    win = find_win(d.screen().root)
    if not win:
        print("ERROR: no window titled '%s*' (is the runner running?)" % WIN_TITLE_MATCH)
        return 1
    print("window id=0x%x name=%r" % (win.id, win.get_wm_name()))

    win.set_input_focus(X.RevertToParent, X.CurrentTime)
    win.configure(stack_mode=X.Above)
    d.sync()
    time.sleep(0.5)

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
