"""Cross-check this implementation against libosdp, an independent OSDP stack.

Skipped automatically when the optional ``libosdp`` package is not installed
(pip install libosdp).
"""

import threading
import time

import pytest

osdp = pytest.importorskip("osdp")

from readerprog import programmer as P  # noqa: E402
from readerprog.osdp.cp import Bus, Reader  # noqa: E402
from readerprog.sim import SimulatedReader, h10301_bits  # noqa: E402


class Pipe:
    def __init__(self):
        self.buf = bytearray()
        self.cv = threading.Condition()

    def put(self, data):
        with self.cv:
            self.buf.extend(data)
            self.cv.notify_all()

    def take(self, n=None, timeout=0.0):
        with self.cv:
            if not self.buf and timeout > 0:
                self.cv.wait(timeout)
            n = len(self.buf) if n is None else min(n, len(self.buf))
            out = bytes(self.buf[:n])
            del self.buf[:n]
            return out


@pytest.fixture
def libosdp_pd():
    cp_to_pd, pd_to_cp = Pipe(), Pipe()

    class PdChannel(osdp.Channel):
        def read(self, max_bytes):
            return cp_to_pd.take(max_bytes)

        def write(self, buf):
            pd_to_cp.put(bytes(buf))
            return len(buf)

        def flush(self):
            pass

    class CpTransport:
        baud = 9600

        def write(self, d):
            cp_to_pd.put(d)

        def read(self, timeout):
            return pd_to_cp.take(None, timeout)

        def set_baud(self, b):
            self.baud = b

        def flush_input(self):
            pd_to_cp.take()

        def close(self):
            pass

    info = osdp.PDInfo(5, PdChannel(), scbk=None, flags=[osdp.LibFlag.InstallMode],
                       id=osdp.PdId(2, 7, 0x00A1B2C3, 0x01020304, 0x00020300))
    caps = osdp.PDCapabilities([
        (osdp.Capability.LEDControl, 1, 1), (osdp.Capability.AudibleControl, 1, 1),
        (osdp.Capability.CommunicationSecurity, 1, 1), (osdp.Capability.CheckCharacter, 1, 0),
        (osdp.Capability.Readers, 0, 1)])
    pd = osdp.PeripheralDevice(info, caps, log_level=osdp.LogLevel.Error)
    pd.start()
    try:
        yield pd, Bus(CpTransport())
    finally:
        pd.stop()


def test_plain_commands(libosdp_pd):
    pd, bus = libosdp_pd
    r = Reader(bus, 5, timeout=0.5)
    assert r.poll() == []
    dev = r.identify()
    assert dev.serial_number == 0x01020304
    assert dev.model == 7 and dev.version == 2
    funcs = {c.function for c in r.capabilities()}
    assert {4, 5, 9}.issubset(funcs)


def test_secure_channel_keyset_and_comset(libosdp_pd):
    pd, bus = libosdp_pd
    r = Reader(bus, 5, timeout=0.5)
    r.poll()
    r.start_secure_channel()  # SCBK-D / install mode
    assert r.poll() == []
    r.led(temp_on_color=2, temp_timer=10)
    cmd = pd.get_command(timeout=2)
    assert cmd["command"] == osdp.Command.LED and cmd["on_color"] == 2 and cmd["timer_count"] == 10
    r.buzzer(count=3)
    cmd = pd.get_command(timeout=2)
    assert cmd["command"] == osdp.Command.Buzzer and cmd["rep_count"] == 3
    assert r.identify().serial_number == 0x01020304  # encrypted reply decrypts

    key = bytes.fromhex("00112233445566778899AABBCCDDEEFF")
    r.keyset(key)
    cmd = pd.get_command(timeout=2)
    assert cmd["command"] == osdp.Command.Keyset and cmd["data"] == key
    time.sleep(0.1)

    r2 = Reader(bus, 5, timeout=0.5)
    r2.poll()
    r2.start_secure_channel(key)
    assert r2.secure_active
    com = r2.comset(9, 9600)
    assert (com.address, com.baud) == (9, 9600)
    cmd = pd.get_command(timeout=2)
    assert cmd["command"] == osdp.Command.Comset and cmd["address"] == 9
    time.sleep(0.3)
    r3 = Reader(bus, 9, timeout=0.5)
    assert r3.poll() == []


def test_program_reader_workflow_against_libosdp(libosdp_pd):
    pd, bus = libosdp_pd
    res = P.program_reader(bus, 5, 9600, 0, 9600)
    assert res.ok and res.verified, res.describe()


def test_simulator_against_libosdp_cp():
    """The demo simulator must look like a real reader to an independent CP."""
    key = bytes.fromhex("000102030405060708090A0B0C0D0E0F")
    sim = SimulatedReader(address=1, baud=9600, scbk=key)
    pending = Pipe()

    class CpChannel(osdp.Channel):
        def read(self, max_bytes):
            return pending.take(max_bytes)

        def write(self, buf):
            pending.put(sim.receive(bytes(buf), 9600))
            return len(buf)

        def flush(self):
            pass

    info = osdp.PDInfo(1, CpChannel(), scbk=key)
    cp = osdp.ControlPanel([info], log_level=osdp.LogLevel.Error)
    cp.start()
    try:
        assert cp.online_wait(1, timeout=8)
        assert cp.sc_wait(1, timeout=8), "libosdp CP could not open a secure channel with the simulator"
        sim.present_card(h10301_bits(12, 345))
        event = cp.get_event(1, timeout=5)
        assert event is not None and event["event"] == osdp.Event.CardRead
        assert event["length"] == 26
    finally:
        cp.teardown()
