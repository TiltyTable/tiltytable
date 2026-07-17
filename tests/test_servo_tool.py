from unittest.mock import call, patch

from calibration import servo_tool


class FakeLink:
    def __init__(self):
        self.events = []

    def set_i2c_address(self, address):
        self.events.append(("module", address))

    def send(self, command):
        self.events.append(("send", command))


def _configs():
    return {
        address: {
            "servos": {
                "0": {"recessed": 1000, "neutral": 1500, "extended": 2000},
                "1": {"recessed": 1100, "neutral": 1600, "extended": 2100},
            }
        }
        for address in servo_tool.BOARD_ORDER
    }


def test_test_position_commands_all_modules_without_added_delays():
    link = FakeLink()

    with patch.object(servo_tool.time, "sleep") as sleep:
        moved = servo_tool.run_test_position(link, _configs(), "all", "neutral")

    assert moved == len(servo_tool.BOARD_ORDER) * 2
    assert [event for event in link.events if event[0] == "module"] == [
        ("module", int(address, 16)) for address in servo_tool.BOARD_ORDER
    ]
    assert sleep.call_args_list == [
        call(servo_tool.SERVO_SETTLE_S)
        for _ in range(len(servo_tool.BOARD_ORDER) * 2)
    ]


def test_test_position_can_target_one_module():
    link = FakeLink()

    with patch.object(servo_tool.time, "sleep") as sleep:
        moved = servo_tool.run_test_position(link, _configs(), "0x43", "extended")

    assert moved == 2
    assert ("module", 0x43) in link.events
    assert ("module", 0x42) not in link.events
    assert sleep.call_args_list == [call(servo_tool.SERVO_SETTLE_S)] * 2


def test_parse_test_module_accepts_all_and_installed_addresses():
    assert servo_tool.parse_test_module("all") == "all"
    assert servo_tool.parse_test_module("67") == "0x43"
