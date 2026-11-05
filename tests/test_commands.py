import json

import pytest

from readerprog import commands as K
from readerprog.osdp import constants as C
from readerprog.osdp import decode as D
from readerprog.osdp import vendors
from readerprog.osdp.cp import Bus, Reader
from readerprog.sim import SIM_VENDOR, SimulatedReader, SimulatedTransport


def make_reader():
    sim = SimulatedReader()
    return sim, Reader(Bus(SimulatedTransport([sim])), 1)


def test_encoders_match_spec_layout():
    assert D.encode_output(0, 5, 0x1234) == bytes([0, 5, 0x34, 0x12])
    assert D.encode_text("HI", 0, 2, 3, 5, mode=4) == bytes([0, 4, 5, 2, 3, 2]) + b"HI"
    assert D.encode_u16(1024) == bytes([0x00, 0x04])
    assert D.encode_mfg(bytes([0, 0x11, 0x12]), b"\x01") == bytes([0, 0x11, 0x12, 1])
    with pytest.raises(ValueError):
        D.encode_mfg(b"\x00\x11", b"")
    with pytest.raises(ValueError):
        D.encode_output(0, 9, 0)


def test_parse_hex_is_forgiving_but_strict():
    assert D.parse_hex("0x00 11-12:AB") == bytes([0, 0x11, 0x12, 0xAB])
    with pytest.raises(ValueError):
        D.parse_hex("123")
    with pytest.raises(ValueError):
        D.parse_hex("zz")
    with pytest.raises(ValueError):
        D.parse_hex("0011", length=3)


@pytest.mark.parametrize("cmd", [c for c in K.STANDARD_COMMANDS if c.key != "comset"], ids=lambda c: c.key)
def test_every_standard_command_is_understood_by_the_simulator(cmd):
    sim, reader = make_reader()
    values = cmd.defaults()
    if cmd.key == "mfg":
        values.update(vendor=SIM_VENDOR.hex(), payload="01 02")
    reply = reader.command(cmd.code, cmd.payload(values), raise_nak=False)
    assert reply.code != C.REPLY_NAK, D.describe_reply(reply.code, reply.data)


def test_led_buzzer_output_text_reach_the_reader():
    sim, reader = make_reader()
    led = K.find_definition("led")
    reader.command(led.code, led.payload({**led.defaults(), "mode": "Permanent", "on_color": "Red"}))
    assert sim.last_led[2] == 1 and sim.last_led[9] == 1  # cancel temp, permanent red
    out = K.find_definition("Output control")
    reader.command(out.code, out.payload(out.defaults()))
    assert sim.outputs[0] == 1
    text = K.find_definition("text")
    reader.command(text.code, text.payload({**text.defaults(), "text": "LOBBY"}))
    assert sim.last_text == "LOBBY"


def test_mfg_reply_is_decoded():
    sim, reader = make_reader()
    mfg = K.find_definition("mfg")
    reply = reader.command(mfg.code, mfg.payload({"vendor": SIM_VENDOR.hex(), "payload": "AA BB"}))
    assert reply.code == C.REPLY_MFGREP
    assert "Manufacturer reply from vendor 5A-5A-5A" in D.describe_reply(reply.code, reply.data)
    assert sim.mfg_received == [b"\xAA\xBB"]
    other = reader.command(mfg.code, mfg.payload({"vendor": "00 11 12", "payload": "01"}), raise_nak=False)
    assert other.code == C.REPLY_NAK


def test_parameter_validation():
    led = K.find_definition("LED control")
    with pytest.raises(ValueError):
        led.payload({**led.defaults(), "on_time": "300"})
    with pytest.raises(ValueError):
        led.payload({**led.defaults(), "on_color": "Purple"})
    with pytest.raises(ValueError):
        K.find_definition("no such command")


def test_describe_reply_variants():
    assert "ACK" in D.describe_reply(C.REPLY_ACK, b"")
    assert "Unknown command" in D.describe_reply(C.REPLY_NAK, b"\x03")
    assert D.describe_reply(C.REPLY_ISTATR, b"\x00\x01") == "Input 0: inactive, Input 1: active"
    assert "address 2, 38400 baud" in D.describe_reply(C.REPLY_COM, bytes([2]) + (38400).to_bytes(4, "little"))
    assert "could not decode" in D.describe_reply(C.REPLY_PDID, b"\x01")


def test_vendor_lookup_with_and_without_registry(monkeypatch):
    assert vendors.vendor_name(bytes([0x00, 0x06, 0x8E])).startswith("HID")
    assert "reversed" in vendors.vendor_name(bytes([0x8E, 0x06, 0x00]))
    monkeypatch.setattr(vendors, "_registry_lookup", lambda oui: None)
    assert vendors.vendor_name(bytes([0x00, 0x11, 0x12])) == "Honeywell CMSS"
    assert vendors.vendor_name(bytes([0x5A, 0x5A, 0x5A])) == "not in the IEEE registry"


def test_user_library_roundtrip_import_and_validation(tmp_path):
    lib = K.CommandLibrary(tmp_path / "lib.json")
    cmd = K.UserCommand(name="Veridt ping", vendor="Veridt", code="80", data="00 11 12 01",
                        notes="from support", source="ticket 123")
    lib.upsert(cmd)
    lib.save()
    again = K.CommandLibrary.load(tmp_path / "lib.json")
    assert again.commands == [cmd]
    d = again.definitions()[0]
    assert d.code == 0x80 and d.payload() == bytes([0, 0x11, 0x12, 1]) and d.user and d.changes_settings
    assert K.find_definition("veridt ping", again).name == "Veridt ping"

    # editing replaces in place, same name+vendor updates instead of duplicating
    lib.upsert(K.UserCommand(name="Veridt ping", vendor="Veridt", code="80", data="00 11 12 02"))
    assert len(lib.commands) == 1 and lib.commands[0].data.endswith("02")

    other = tmp_path / "shared.json"
    other.write_text(json.dumps({"commands": [
        {"name": "HID thing", "vendor": "HID", "code": "80", "data": "00 06 8E 10", "unknown_field": 1}]}))
    assert lib.import_file(other) == 1
    assert {c.vendor for c in lib.commands} == {"Veridt", "HID"}

    for bad in (dict(name=""), dict(vendor="Acme"), dict(code="12"), dict(code="80", data="00 11"),
                dict(data="xyz")):
        fields = dict(name="x", vendor="Other", code="80", data="00 11 12")
        fields.update(bad)
        with pytest.raises(ValueError):
            K.UserCommand(**fields).validate()

    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"commands": [{"name": "x", "vendor": "Other", "code": "zz"}]}))
    with pytest.raises(ValueError):
        K.CommandLibrary.load(broken)
