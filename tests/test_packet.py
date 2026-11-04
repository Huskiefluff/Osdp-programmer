import pytest

from readerprog.osdp import constants as C
from readerprog.osdp.crc import checksum8, crc16
from readerprog.osdp.packet import PacketError, PacketReader, build_packet, parse_packet


def test_crc_check_value():
    # CRC-16/AUG-CCITT check value used by OSDP
    assert crc16(b"123456789") == 0xE5CC


def test_known_poll_packet():
    # osdp_POLL to address 1, SQN 0, CRC: 53 01 08 00 04 60 + CRC
    pkt = build_packet(1, C.CMD_POLL, sqn=0)
    assert pkt[:6] == bytes([0x53, 0x01, 0x08, 0x00, 0x04, 0x60])
    assert len(pkt) == 8
    parsed = parse_packet(pkt)
    assert parsed.code == C.CMD_POLL and parsed.address == 1 and not parsed.is_reply


def test_roundtrip_reply_with_checksum():
    pkt = build_packet(5, C.REPLY_PDID, bytes(range(12)), sqn=2, use_crc=False, is_reply=True)
    assert checksum8(pkt[:-1]) == pkt[-1]
    p = parse_packet(pkt)
    assert p.is_reply and p.address == 5 and p.sqn == 2 and p.data == bytes(range(12))


def test_bad_crc_rejected():
    pkt = bytearray(build_packet(1, C.CMD_ID, b"\x00"))
    pkt[-1] ^= 0xFF
    with pytest.raises(PacketError):
        parse_packet(bytes(pkt))


def test_reader_handles_noise_echo_and_split_chunks():
    cmd = build_packet(1, C.CMD_POLL)
    reply = build_packet(1, C.REPLY_ACK, is_reply=True)
    stream = b"\x00\xFF\x12" + b"\xFF" + cmd + b"\x53\x99" + b"\xFF" + reply
    r = PacketReader()
    out = []
    for i in range(0, len(stream), 3):
        out += r.feed(stream[i:i + 3])
    assert [p.code for p in out] == [C.CMD_POLL, C.REPLY_ACK]
    assert out[1].is_reply


def test_mac_requires_secure_block():
    with pytest.raises(ValueError):
        build_packet(1, C.CMD_POLL, mac_func=lambda b: b"\x00" * 4)
