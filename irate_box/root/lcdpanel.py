# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The 480x320 ILI9488 on SPI1: a static test card, on/off, and the one-minute animation sequence.

The overlay (lcdtouch.py) puts the display on spidev1.0; this is the userspace side that initialises it,
because there is no kernel display driver. D/C is GPIO0 line 9 (header 26), RST GPIO1 line 27 (header 28).
18-bit RGB666 over SPI, MADCTL 0x68 (landscape and BGR), 10 MHz. Stdlib, spidev and gpiod.
"""

import colorsys
import os
import time
from pathlib import Path

W, H = 480, 320
DC_CHIP = "/dev/gpiochip0"
DC = 9
RST_CHIP = "/dev/gpiochip1"
RST = 27
SPEED = 10_000_000
BL = Path(os.environ.get("HUB_LCD_BL", "/sys/class/leds/lcd-backlight/brightness"))


def set_bl(on):
    """The backlight, through the gpio-leds node the overlay adds (header pin 36)."""
    try:
        BL.write_text("1" if on else "0")
    except OSError:
        pass


def rgb(r, g, b):
    return bytes((min(255, max(0, r)), min(255, max(0, g)), min(255, max(0, b))))


def wheel(i):
    r, g, b = colorsys.hsv_to_rgb((i % 36) / 36.0, 1, 1)
    return rgb(int(r * 252), int(g * 252), int(b * 252))


BLACK = rgb(0, 0, 0)
RED = rgb(252, 0, 0)
GREEN = rgb(0, 252, 0)
BLUE = rgb(0, 0, 252)
WHITE = rgb(252, 252, 252)


class Panel:
    def __init__(self):
        import gpiod
        import spidev

        self._gpiod = gpiod
        self.spi = spidev.SpiDev()
        self.spi.open(1, 0)
        self.spi.mode = 0
        self.spi.max_speed_hz = SPEED
        output = gpiod.LineSettings(
            direction=gpiod.line.Direction.OUTPUT,
            output_value=gpiod.line.Value.ACTIVE,
        )
        self.gpio_dc = gpiod.request_lines(DC_CHIP, consumer="irate-box-lcd", config={DC: output})
        self.gpio_rst = gpiod.request_lines(RST_CHIP, consumer="irate-box-lcd", config={RST: output})

    def dc(self, high):
        v = self._gpiod.line.Value
        self.gpio_dc.set_value(DC, v.ACTIVE if high else v.INACTIVE)

    def rst(self, high):
        v = self._gpiod.line.Value
        self.gpio_rst.set_value(RST, v.ACTIVE if high else v.INACTIVE)

    def cmd(self, c, data=None):
        self.dc(False)
        self.spi.writebytes([c])
        if data:
            self.dc(True)
            self.spi.writebytes(list(data))

    def init(self):
        self.rst(False)
        time.sleep(0.05)
        self.rst(True)
        time.sleep(0.2)
        self.cmd(0x01)
        time.sleep(0.15)
        self.cmd(0x11)
        time.sleep(0.15)
        self.cmd(0x3A, [0x66])
        self.cmd(0x36, [0x68])
        self.cmd(0x21)
        self.cmd(0x13)
        self.cmd(0x29)
        set_bl(True)
        time.sleep(0.1)

    def window(self, x0, y0, x1, y1):
        self.cmd(0x2A, [x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF])
        self.cmd(0x2B, [y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF])
        self.cmd(0x2C)

    def fill(self, px, x0=0, y0=0, x1=W - 1, y1=H - 1):
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W - 1, x1), min(H - 1, y1)
        if x1 < x0 or y1 < y0:
            return
        self.window(x0, y0, x1, y1)
        self.dc(True)
        per = 4096 // len(px)
        chunk = px * per
        left = (x1 - x0 + 1) * (y1 - y0 + 1)
        while left:
            take = min(left, per)
            self.spi.writebytes(list(chunk[: take * len(px)]))
            left -= take

    def card(self):
        self.fill(WHITE)
        for i, color in enumerate(
            (WHITE, rgb(252, 252, 0), rgb(0, 252, 252), GREEN, rgb(252, 0, 252), RED, BLUE, BLACK)
        ):
            self.fill(color, i * 60, 0, (i + 1) * 60 - 1, H - 1)
        self.fill(RED, 190, 110, 289, 209)

    def off(self):
        set_bl(False)
        try:
            self.fill(BLACK)
            self.cmd(0x28)
            time.sleep(0.05)
            self.cmd(0x10)
        except Exception:  # noqa: BLE001, B110 - blanking is best effort # nosec B110
            pass

    def close(self):
        try:
            self.gpio_dc.release()
        finally:
            try:
                self.gpio_rst.release()
            finally:
                self.spi.close()

    def run(self, seconds=60):
        start = time.monotonic()
        deadline = start + seconds

        end = min(deadline, start + 6)
        i = 0
        while time.monotonic() < end:
            self.fill(wheel(i * 2))
            i += 1
            time.sleep(0.1)

        self.fill(BLACK)
        x, y, dx, dy, s = 40, 40, 4, 3, 80
        self.fill(RED, x, y, x + s - 1, y + s - 1)
        end = min(deadline, start + 26)
        i = 0
        while time.monotonic() < end:
            ox, oy = x, y
            nx, ny = x + dx, y + dy
            if nx < 0 or nx + s > W:
                dx = -dx
                nx = x + dx
            if ny < 0 or ny + s > H:
                dy = -dy
                ny = y + dy
            self.fill(wheel(i // 6), nx, ny, nx + s - 1, ny + s - 1)
            top = min(oy + s - 1, ny - 1)
            if top >= oy:
                self.fill(BLACK, ox, oy, ox + s - 1, top)
            bottom = max(oy, ny + s)
            if bottom <= oy + s - 1:
                self.fill(BLACK, ox, bottom, ox + s - 1, oy + s - 1)
            mid_top, mid_bottom = max(oy, ny), min(oy + s - 1, ny + s - 1)
            if mid_bottom >= mid_top:
                if ox < nx:
                    self.fill(BLACK, ox, mid_top, min(ox + s - 1, nx - 1), mid_bottom)
                if ox + s > nx + s:
                    self.fill(BLACK, max(ox, nx + s), mid_top, ox + s - 1, mid_bottom)
            x, y = nx, ny
            i += 1
            time.sleep(0.01)

        self.fill(BLACK)
        y, dy = 0, 6
        end = min(deadline, start + 38)
        while time.monotonic() < end:
            self.fill(BLACK, 0, max(0, y - 6), W - 1, min(H - 1, y + 3))
            y += dy
            if y <= 0 or y >= H - 4:
                dy = -dy
                y = max(0, min(H - 4, y))
            self.fill(rgb(0, 252, 252), 0, y, W - 1, y + 3)
            time.sleep(0.008)

        self.fill(BLACK)
        old = 0
        end = min(deadline, start + 56)
        i = 0
        while time.monotonic() < end:
            phase = (i % 160) / 80.0
            size = 40 + int(160 * (1 - abs(phase - 1)))
            color = (RED, GREEN, BLUE)[(i // 40) % 3]
            ox, oy = (W - old) // 2, (H - old) // 2
            nx, ny = (W - size) // 2, (H - size) // 2
            self.fill(color, nx, ny, nx + size - 1, ny + size - 1)
            if old > size:
                self.fill(BLACK, ox, oy, ox + old - 1, ny - 1)
                self.fill(BLACK, ox, ny + size, ox + old - 1, oy + old - 1)
                self.fill(BLACK, ox, ny, nx - 1, ny + size - 1)
                self.fill(BLACK, nx + size, ny, ox + old - 1, ny + size - 1)
            old = size
            i += 1
            time.sleep(0.012)

        self.card()
        while time.monotonic() < deadline:
            time.sleep(0.1)
        self.off()


def _through_service(op, **fields):
    """The display service owns the panel when it runs; None when there is no service to ask."""
    from irate_box.root import hardware_service

    answer = hardware_service.request(op, **fields)
    if answer is None:
        return None
    if not answer.get("ok"):
        raise RuntimeError(answer.get("message") or "The display service refused the change.")
    return answer


def show_card():
    if _through_service("card") is not None:
        return
    p = Panel()
    try:
        p.init()
        p.card()
    finally:
        p.close()


def off():
    if _through_service("off") is not None:
        return
    p = Panel()
    try:
        p.off()
    finally:
        p.close()


def run_test(seconds=60):
    if _through_service("test", seconds=seconds) is not None:
        return
    p = Panel()
    try:
        p.init()
        p.run(seconds)
    finally:
        p.close()
