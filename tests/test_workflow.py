"""End-to-end workflows against the built-in simulated Veridt-style reader."""

import os

import pytest

from readerprog import programmer as P
from readerprog.osdp import constants as C
from readerprog.osdp.cp import Bus, NakError, Reader, scan
from readerprog.osdp.decode import decode_card_bits
from readerprog.osdp.secure import SecureChannelError
from readerprog.sim import SimulatedReader, SimulatedTransport, fascn_200_bits, h10301_bits


def make(address=1, baud=9600, line_baud=9600, echo=False, **kw):
    sim = SimulatedReader(address=address, baud=baud, **kw)
    bus = Bus(SimulatedTransport([sim], baud=line_baud, echo=echo))
    return sim, bus


@pytest.mark.parametrize("baud", C.BAUD_RATES)
def test_scan_finds_reader_at_any_baud(baud):
    sim, bus = make(address=37, baud=baud, line_baud=9600)
    found = scan(bus)
    assert len(found) == 1
    assert (found[0].address, found[0].baud) == (37, baud)
    assert found[0].device_id.serial_number == sim.serial_number


def test_scan_without_broadcast_and_with_echo():
    sim, bus = make(address=3, baud=19200, echo=True)
    found = scan(bus, use_broadcast=False)
    assert (found[0].address, found[0].baud) == (3, 19200)


def test_scan_nothing_connected():
    bus = Bus(SimulatedTransport([]))
    assert scan(bus) == []


def test_program_to_onguard_settings_and_survive_power_cycle():
    sim, bus = make()
    res = P.program_reader(bus, 1, 9600, 0, 9600)
    assert res.ok and res.verified
    sim.power_cycle()
    v = P.verify_after_power_cycle(bus, 0, 9600, sim.serial_number)
    assert v.ok, v.text


def test_baud_change_without_address_change_is_lost_after_power_cycle():
    # Veridt Tech Note 123 behaviour reproduced by the simulator.
    sim, bus = make(address=5, baud=9600)
    warnings = P.comset_warnings(5, 5, 38400)
    assert warnings
    res = P.program_reader(bus, 5, 9600, 5, 38400)
    assert res.ok and res.verified and res.warnings
    sim.power_cycle()
    v = P.verify_after_power_cycle(bus, 5, 38400, sim.serial_number)
    assert not v.ok
    assert v.found and (v.found.address, v.found.baud) == (5, 9600)


def test_address_and_baud_change_together_persist():
    sim, bus = make()
    res = P.program_reader(bus, 1, 9600, 2, 38400)
    assert res.verified and not res.warnings
    sim.power_cycle()
    assert P.verify_after_power_cycle(bus, 2, 38400).ok


def test_default_address_always_restarts_at_9600():
    sim, bus = make(address=4, baud=9600)
    assert P.comset_warnings(4, 1, 19200)
    P.program_reader(bus, 4, 9600, 1, 19200)
    sim.power_cycle()
    assert (sim.address, sim.baud) == (1, 9600)


def test_read_info():
    sim, bus = make()
    info = P.read_info(bus, 1, 9600)
    assert info.device_id.serial_number == sim.serial_number
    assert info.supports_secure_channel is True
    assert "Communication security" in info.describe()


def test_secure_channel_and_key_install():
    sim, bus = make()
    assert "default install key" in P.test_secure_channel(bus, 1, 9600, None)
    key = os.urandom(16)
    msg = P.install_key(bus, 1, 9600, key)
    assert "installed and verified" in msg
    assert sim.scbk == key
    # Default key no longer accepted, custom key is.
    with pytest.raises(SecureChannelError):
        P.test_secure_channel(bus, 1, 9600, None)
    assert "custom key" in P.test_secure_channel(bus, 1, 9600, key)
    # Wrong custom key is detected.
    with pytest.raises(SecureChannelError):
        P.test_secure_channel(bus, 1, 9600, bytes(16))


def test_program_over_secure_channel():
    key = bytes.fromhex("00112233445566778899AABBCCDDEEFF")
    sim, bus = make(scbk=key)
    res = P.program_reader(bus, 1, 9600, 0, 9600, key=key, use_secure=True)
    assert res.verified


def test_encrypted_commands_and_events():
    sim, bus = make()
    r = Reader(bus, 1)
    r.start_secure_channel()
    r.led(temp_on_color=2)
    assert sim.last_led[2] == 2 and sim.last_led[5] == 2
    r.buzzer()
    assert sim.beeps == 1
    sim.present_card(h10301_bits(123, 4567))
    events = r.poll()
    assert events[0].card.decoded["Facility code"] == 123
    assert events[0].card.decoded["Card number"] == 4567
    assert events[0].card.decoded["Parity"] == "OK"


def test_keyset_requires_secure_channel():
    sim, bus = make()
    r = Reader(bus, 1)
    with pytest.raises(NakError) as exc:
        r.command(C.CMD_KEYSET, bytes([1, 16]) + bytes(16))
    assert exc.value.code == 0x06


def test_unknown_command_nak():
    sim, bus = make()
    r = Reader(bus, 1)
    reply = r.manufacturer(b"\x5A\x5A\x5A", b"\x01")
    assert reply.code == C.REPLY_NAK and reply.data[0] == 0x03


def test_fascn_200_decode():
    bits = fascn_200_bits("9999", "1234", "123456", pi="0123456789", oi="5678")
    d = decode_card_bits(bits)
    assert d["Agency code"] == "9999"
    assert d["System code"] == "1234"
    assert d["Credential number"] == "123456"
    assert d["Person identifier"] == "0123456789"


def test_onguard_checklist_contents():
    text = P.onguard_checklist(0, 9600, card_bits=200)
    assert "OSDP address                 : 0" in text
    assert "9600" in text and "200 bits" in text


def test_programming_log(tmp_path):
    path = P.append_log({"port": "COM3", "action": "program", "new_address": 0}, tmp_path)
    P.append_log({"port": "COM3", "action": "verify"}, tmp_path)
    lines = path.read_text().splitlines()
    assert lines[0].startswith("timestamp,port,action")
    assert len(lines) == 3
