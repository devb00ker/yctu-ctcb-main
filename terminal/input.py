import time
from .protocol import EXAMPLES


class InputState:
    def __init__(self):
        self.active = False
        self.keys = set()
        self.buttons = set()
        self.dx = self.dy = self.dz = 0
        self.last_send = 0

    def clear(self):
        self.active = False
        self.keys.clear()
        self.buttons.clear()
        self.dx = self.dy = self.dz = 0

    def packet(self, scale=1.0, y_sign=-1):
        def clamp(v):
            return max(-(2**31), min(2**31 - 1, round(v)))
        return dict(mouse_x=clamp(self.dx * scale), mouse_y=clamp(self.dy * scale * y_sign),
                    mouse_z=clamp(self.dz), keyboard_value=sum(1 << k for k in self.keys),
                    left_button_down=1 in self.buttons, right_button_down=2 in self.buttons,
                    mid_button_down=4 in self.buttons)

    def tick(self, transport, config):
        if not self.active or time.monotonic() - self.last_send < 1 / config.control_hz:
            return
        transport.send("KeyboardMouseControl", self.packet(config.mouse_scale, config.mouse_y_sign))
        self.dx = self.dy = self.dz = 0
        self.last_send = time.monotonic()


ZERO_INPUT = EXAMPLES["KeyboardMouseControl"].copy()
