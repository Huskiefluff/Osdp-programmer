import pytest

from readerprog.osdp.constants import SCBK_DEFAULT
from readerprog.osdp.secure import SecureChannelError, SecureSession, parse_key

RND_A = bytes.fromhex("0102030405060708")
RND_B = bytes.fromhex("1112131415161718")


def _pair(key=SCBK_DEFAULT):
    cp = SecureSession(key, RND_A)
    pd = SecureSession(key, RND_A)
    sc = cp.server_cryptogram(RND_B)
    assert pd.client_cryptogram(RND_B) == cp.client_cryptogram(RND_B)
    cp.establish(RND_B, sc)
    pd.establish(RND_B, sc)
    return cp, pd


def test_known_answer_vectors():
    # Regression vectors; the algorithm itself is cross-checked against libosdp in
    # test_libosdp_interop.py.
    s = SecureSession(SCBK_DEFAULT, RND_A)
    assert s.s_enc.hex() == KAT["s_enc"]
    assert s.s_mac1.hex() == KAT["s_mac1"]
    assert s.s_mac2.hex() == KAT["s_mac2"]
    assert s.client_cryptogram(RND_B).hex() == KAT["client"]
    assert s.initial_rmac(s.server_cryptogram(RND_B)).hex() == KAT["rmac_i"]


def test_command_and_reply_chain():
    cp, pd = _pair()
    for payload in (b"", b"\x01", bytes(15), bytes(16), bytes(range(40))):
        msg = b"\x53\x01\x20\x00\x0d\x02\x17\x69" + payload
        enc = cp.encrypt_command_data(payload) if payload else b""
        if payload:
            assert pd.decrypt_command_data(enc) == payload
        assert cp.mac_command(msg) == pd.mac_command(msg)
        renc = pd.encrypt_reply_data(payload) if payload else b""
        if payload:
            assert cp.decrypt_reply_data(renc) == payload
        assert pd.mac_reply(msg) == cp.mac_reply(msg)


def test_wrong_key_detected():
    a = SecureSession(SCBK_DEFAULT, RND_A)
    b = SecureSession(bytes(16), RND_A)
    assert a.client_cryptogram(RND_B) != b.client_cryptogram(RND_B)


def test_bad_padding_raises():
    cp, pd = _pair()
    with pytest.raises(SecureChannelError):
        cp.decrypt_reply_data(bytes(16))


def test_parse_key():
    assert parse_key("default") == SCBK_DEFAULT
    assert parse_key("00 11 22 33 44 55 66 77 88 99 AA BB CC DD EE FF") == bytes.fromhex(
        "00112233445566778899AABBCCDDEEFF")
    with pytest.raises(ValueError):
        parse_key("1234")
    with pytest.raises(ValueError):
        parse_key("zz" * 16)


KAT = {
    "s_enc": "9088006279d41b618acaf4760e9deec6",
    "s_mac1": "336a2fd840a10efbb5c322397b9759ef",
    "s_mac2": "fb271d4a53cd634a17cdac9c9adc86bd",
    "client": "5963e6947734e9b2701c700e9dd04116",
    "rmac_i": "8695457f1fba99c0ff42066cd7133027",
}
