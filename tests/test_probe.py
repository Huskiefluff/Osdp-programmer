from readerprog.osdp import constants as C
from readerprog.osdp.cp import Bus, Reader
from readerprog.osdp.packet import PacketReader
from readerprog.osdp.probe import (NEEDS_SECURE, NO_REPLY, NOT_PROBED, NOT_SUPPORTED, SKIPPED, SUPPORTED,
                                   UNCLEAR, classify, probe_commands, report)
from readerprog.sim import SimulatedReader, SimulatedTransport


class RecordingTransport(SimulatedTransport):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.sent = []
        self._parser = PacketReader()

    def write(self, data):
        self.sent += [p.code for p in self._parser.feed(data)]
        super().write(data)


class SilentOnXwr(SimulatedReader):
    def _handle(self, pkt):
        if pkt.code == C.CMD_XWR:
            return b""  # pretend the reader ignores this command entirely
        return super()._handle(pkt)


def by_code(results):
    return {r.code: r.status for r in results}


def test_classify():
    assert classify(C.REPLY_NAK, b"\x03") == NOT_SUPPORTED
    assert classify(C.REPLY_NAK, b"\x02") == SUPPORTED
    assert classify(C.REPLY_NAK, b"\x09") == SUPPORTED
    assert classify(C.REPLY_NAK, b"\x06") == NEEDS_SECURE
    assert classify(C.REPLY_NAK, b"\x01") == UNCLEAR
    assert classify(C.REPLY_ACK, b"") == SUPPORTED
    assert classify(C.REPLY_PDID, bytes(12)) == SUPPORTED


def test_probe_on_simulator_is_safe_and_correct():
    sim = SimulatedReader(address=3, baud=9600)
    transport = RecordingTransport([sim])
    results = probe_commands(Reader(Bus(transport), 3))
    status = by_code(results)
    assert status[C.CMD_POLL] == SUPPORTED and status[C.CMD_LED] == SUPPORTED
    assert status[C.CMD_MFG] == SUPPORTED
    assert status[C.CMD_BIOREAD] == NOT_SUPPORTED and status[C.CMD_XWR] == NOT_SUPPORTED
    for code in NOT_PROBED:
        assert status[code] == SKIPPED
        assert code not in transport.sent, f"{C.command_name(code)} must never be sent"
    # nothing about the reader changed
    assert (sim.address, sim.baud, sim.scbk) == (3, 9600, None)
    assert sim.beeps == 0 and sim.last_led is None and sim.outputs == [0]
    text = report(results, "hdr")
    assert "Supported: osdp_POLL" in text and "NAK 03" in text


def test_probe_survives_a_command_the_reader_ignores():
    sim = SilentOnXwr()
    reader = Reader(Bus(SimulatedTransport([sim])), 1)
    reader.retries = 0
    status = by_code(probe_commands(reader))
    assert status[C.CMD_XWR] == NO_REPLY
    assert status[C.CMD_ABORT] == SUPPORTED  # later commands still work


def test_probe_can_be_cancelled():
    reader = Reader(Bus(SimulatedTransport([SimulatedReader()])), 1)
    results = probe_commands(reader, cancelled=lambda: True)
    assert all(r.status == SKIPPED for r in results)
