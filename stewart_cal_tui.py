"""Curses TUI for Stewart per-axis crank calibration."""

from __future__ import annotations

import curses
import re
import sys
import time
from dataclasses import dataclass, field

import serial

DEFAULT_JOG_FINE = 200
DEFAULT_JOG_COARSE = 1600


@dataclass
class StewartStatus:
    calibrated: bool = False
    bench: bool = False
    moving: bool = False
    axis_marked: list[bool] = field(default_factory=lambda: [False, False, False])
    axis_deg: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    raw: str = ""


def parse_status(text: str) -> StewartStatus:
    st = StewartStatus(raw=text)
    if m := re.search(r"calibrated\s+(\d)", text):
        st.calibrated = m.group(1) == "1"
    if m := re.search(r"bench\s+(\d)", text):
        st.bench = m.group(1) == "1"
    if m := re.search(r"moving\s+(\d)", text):
        st.moving = m.group(1) == "1"
    for i in range(3):
        if m := re.search(rf"axis{i}_marked\s+(\d)", text):
            st.axis_marked[i] = m.group(1) == "1"
        if m := re.search(rf"axis{i}_deg\s+([-0-9.]+)", text):
            st.axis_deg[i] = float(m.group(1))
    return st


class StewartCalSession:
    """Quiet serial helper for the calibration TUI."""

    def __init__(self, ser: serial.Serial) -> None:
        self.ser = ser
        self.last_reply: list[str] = []

    def exchange(self, cmd: str, wait: float = 0.55) -> list[str]:
        self.ser.reset_input_buffer()
        self.ser.write((cmd.rstrip() + "\n").encode())
        self.ser.flush()
        end = time.time() + wait
        lines: list[str] = []
        while time.time() < end:
            raw = self.ser.readline()
            if not raw:
                continue
            line = raw.decode("utf-8", "replace").strip()
            if line:
                lines.append(line)
                end = time.time() + 0.12
        self.last_reply = lines
        return lines

    def ok_prefix(self, lines: list[str], prefix: str) -> bool:
        return any(line.startswith(prefix) for line in lines)

    def status(self) -> StewartStatus:
        lines = self.exchange("status", wait=0.4)
        return parse_status(" ".join(lines))

    def wait_idle(self, timeout_s: float = 45.0) -> None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            st = self.status()
            if not st.moving:
                return
            time.sleep(0.08)

    def jog(self, axis: int, pulses: int) -> None:
        self.exchange(f"jog {axis} {pulses}", wait=0.25)
        self.wait_idle()


class StewartCalTUI:
    def __init__(
        self,
        session: StewartCalSession,
        *,
        jog_fine: int = DEFAULT_JOG_FINE,
        jog_coarse: int = DEFAULT_JOG_COARSE,
        skip_intro: bool = False,
    ) -> None:
        self.session = session
        self.jog_fine = jog_fine
        self.jog_coarse = jog_coarse
        self.skip_intro = skip_intro
        self.axis = 0
        self.msg = ""
        self.done = False
        self.aborted = False
        self.status = StewartStatus()

    def _set_msg(self, text: str) -> None:
        self.msg = text[:120]

    def _draw_intro(self, stdscr: curses.window) -> None:
        C = curses
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        bold = C.A_BOLD
        lines = [
            "STEWART CALIBRATION",
            "",
            "For each leg (axis 0 → 1 → 2):",
            "  • Jog the crank until the pin points straight UP (vertical / max heave).",
            "  • Press Enter when it looks vertical by eye.",
            "",
            "Controls:",
            "  ← / →     fine jog",
            "  ↑ / ↓     coarse jog",
            "  Enter     mark this axis vertical",
            "  s         refresh status",
            "  q         abort (motors off)",
            "",
            "Press Enter to start…",
        ]
        y = max(1, (h - len(lines)) // 2)
        for line in lines:
            x = max(2, (w - len(line)) // 2)
            stdscr.addnstr(y, x, line, max(0, w - x - 1), bold if line == lines[0] else 0)
            y += 1
        stdscr.refresh()

    def _draw_main(self, stdscr: curses.window) -> None:
        C = curses
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        bold = C.A_BOLD
        cur_a = C.color_pair(1) | bold if C.has_colors() else bold
        ok_a = C.color_pair(2) if C.has_colors() else 0
        warn_a = C.color_pair(3) if C.has_colors() else 0

        def put(y: int, x: int, text: str, attr: int = 0) -> None:
            if 0 <= y < h and 0 <= x < w:
                try:
                    stdscr.addnstr(y, x, text, max(0, w - x - 1), attr)
                except C.error:
                    pass

        put(0, 2, "STEWART CRANK CALIBRATION", bold)
        put(0, w - 22, f"axis {self.axis} / 2", cur_a)

        put(2, 2, "Leg status:", bold)
        for i in range(3):
            if self.status.axis_marked[i]:
                label = f"  Axis {i}   DONE (vertical recorded)"
                attr = ok_a
            elif i == self.axis:
                label = f"  Axis {i}   >> jog now — crank straight UP"
                attr = cur_a
            elif i < self.axis:
                label = f"  Axis {i}   DONE"
                attr = ok_a
            else:
                label = f"  Axis {i}   waiting"
                attr = 0
            put(3 + i, 2, label, attr)

        put(7, 2, "Live crank angle (deg from firmware model):", bold)
        for i in range(3):
            deg = self.status.axis_deg[i]
            move = "  moving" if (i == self.axis and self.status.moving) else ""
            put(8 + i, 4, f"axis {i}: {deg:+8.2f}°{move}", cur_a if i == self.axis else 0)

        put(12, 2, "Target pose when marked:", bold)
        put(13, 4, "Crank pin straight UP  (max heave reference)", 0)
        put(14, 4, "Expected step label ≈ -90° delta from horizontal neutral", 0)

        # ASCII crank hint
        put(16, 2, "        | pin", 0)
        put(17, 2, "        |", 0)
        put(18, 2, "       /|\\   ← vertical", ok_a)
        put(19, 2, "      —O—", 0)
        put(20, 2, "     motor", 0)

        msg_attr = warn_a if self.msg.startswith("!") else 0
        put(h - 4, 2, self.msg, msg_attr)
        put(h - 2, 2, "←/→ fine jog   ↑/↓ coarse jog   Enter=vertical OK   s=status   q=quit", 0)
        stdscr.refresh()

    def _draw_done(self, stdscr: curses.window) -> None:
        C = curses
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        bold = C.A_BOLD
        ok_a = C.color_pair(2) | bold if C.has_colors() else bold
        lines = [
            "Calibration complete.",
            "",
            "All three cranks recorded at vertical (max heave).",
            "",
            "Press any key to exit…",
        ]
        y = max(2, (h - len(lines)) // 2)
        for line in lines:
            x = max(2, (w - len(line)) // 2)
            attr = ok_a if "complete" in line else 0
            stdscr.addnstr(y, x, line, max(0, w - x - 1), attr)
            y += 1
        stdscr.refresh()

    def _confirm_axis(self) -> bool:
        self.session.exchange(f"disable {self.axis}", wait=0.35)
        lines = self.session.exchange(f"cal_axis {self.axis}", wait=0.7)
        if not self.session.ok_prefix(lines, "OK cal_axis"):
            err = next((l for l in lines if l.startswith("ERR")), lines[-1] if lines else "no reply")
            self._set_msg(f"! cal_axis failed: {err}")
            self.session.exchange(f"enable {self.axis}", wait=0.35)
            return False
        self._set_msg(f"Axis {self.axis} marked vertical")
        return True

    def _handle_key(self, key: int) -> bool:
        """Return False to exit loop (abort or done screen)."""
        C = curses
        if key in (ord("q"), ord("Q"), 27):
            self.aborted = True
            self.session.exchange("disable", wait=0.4)
            return False
        if key in (ord("s"), ord("S")):
            self.status = self.session.status()
            self._set_msg("Status refreshed")
            return True
        if key in (C.KEY_RIGHT, ord("l"), ord("L")):
            self.session.jog(self.axis, self.jog_fine)
            self.status = self.session.status()
            self._set_msg(f"Fine jog +{self.jog_fine} pulses")
            return True
        if key in (C.KEY_LEFT, ord("h"), ord("H")):
            self.session.jog(self.axis, -self.jog_fine)
            self.status = self.session.status()
            self._set_msg(f"Fine jog -{self.jog_fine} pulses")
            return True
        if key in (C.KEY_UP, ord("k"), ord("K")):
            self.session.jog(self.axis, self.jog_coarse)
            self.status = self.session.status()
            self._set_msg(f"Coarse jog +{self.jog_coarse} pulses")
            return True
        if key in (C.KEY_DOWN, ord("j"), ord("J")):
            self.session.jog(self.axis, -self.jog_coarse)
            self.status = self.session.status()
            self._set_msg(f"Coarse jog -{self.jog_coarse} pulses")
            return True
        if key in (ord(" "),):
            self.session.jog(self.axis, self.jog_fine)
            self.status = self.session.status()
            return True
        if key in (10, 13, C.KEY_ENTER):
            if self._confirm_axis():
                if self.axis >= 2:
                    lines = self.session.exchange("cal_finish", wait=0.7)
                    if self.session.ok_prefix(lines, "OK cal_finish"):
                        self.status = self.session.status()
                        self.done = True
                        return False
                    err = next((l for l in lines if l.startswith("ERR")), "cal_finish failed")
                    self._set_msg(f"! {err}")
                else:
                    self.axis += 1
                    en = self.session.exchange(f"enable {self.axis}", wait=0.5)
                    if any(l.startswith("ERR") for l in en):
                        self._set_msg(f"! enable axis {self.axis} failed")
                        self.aborted = True
                        return False
                    self.status = self.session.status()
                    self._set_msg(f"Now jog axis {self.axis} to vertical")
            return True
        self._set_msg("Use arrow keys, Enter, s, or q")
        return True

    def run_curses(self, stdscr: curses.window) -> None:
        C = curses
        curses.curs_set(0)
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, C.COLOR_BLACK, C.COLOR_CYAN)
            curses.init_pair(2, C.COLOR_GREEN, -1)
            curses.init_pair(3, C.COLOR_YELLOW, -1)
        stdscr.keypad(True)
        stdscr.nodelay(False)
        h, w = stdscr.getmaxyx()
        if h < 22 or w < 68:
            self._set_msg(f"! Terminal too small ({w}x{h}); need ~68x22")
            self.aborted = True
            stdscr.addstr(0, 0, self.msg[: w - 1])
            stdscr.refresh()
            stdscr.getch()
            return

        if not self.skip_intro:
            self._draw_intro(stdscr)
            while True:
                key = stdscr.getch()
                if key in (10, 13, C.KEY_ENTER, ord(" "), ord("\n")):
                    break
                if key in (ord("q"), 27):
                    self.aborted = True
                    return

        lines = self.session.exchange("cal_begin", wait=0.7)
        if not self.session.ok_prefix(lines, "OK cal_begin"):
            self._set_msg("! cal_begin failed — flash latest Stewart firmware?")
            self.aborted = True
            self._draw_main(stdscr)
            stdscr.getch()
            return

        en = self.session.exchange("enable 0", wait=0.5)
        if any(l.startswith("ERR") for l in en):
            self._set_msg("! enable axis 0 failed")
            self.aborted = True
            return

        self.status = self.session.status()
        self._set_msg("Jog axis 0 — crank pin straight UP, then Enter")

        while not self.done and not self.aborted:
            self._draw_main(stdscr)
            if not self._handle_key(stdscr.getch()):
                break

        if self.done:
            self._draw_done(stdscr)
            stdscr.getch()


def run_interactive_calibration_tui(
    ser: serial.Serial,
    *,
    jog_fine: int = DEFAULT_JOG_FINE,
    jog_coarse: int = DEFAULT_JOG_COARSE,
    skip_intro: bool = False,
) -> bool:
    if not sys.stdin.isatty():
        print(
            "Interactive calibration TUI requires a terminal (TTY). "
            "Use stewart_calibrate.py --legacy on headless hosts.",
            file=sys.stderr,
        )
        return False

    session = StewartCalSession(ser)
    tui = StewartCalTUI(
        session,
        jog_fine=jog_fine,
        jog_coarse=jog_coarse,
        skip_intro=skip_intro,
    )
    try:
        curses.wrapper(tui.run_curses)
    except curses.error as exc:
        print(f"TUI error (terminal too small?): {exc}", file=sys.stderr)
        session.exchange("disable", wait=0.3)
        return False

    if tui.aborted:
        session.exchange("disable", wait=0.3)
        return False
    return tui.done and tui.status.calibrated
