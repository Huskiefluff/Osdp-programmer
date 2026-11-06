from readerprog.cli import main


def test_cli_simulated_flow(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert main(["scan", "--simulate"]) == 0
    assert "address 1, 9600 baud" in capsys.readouterr().out
    assert main(["info", "--simulate"]) == 0
    assert "Serial number" in capsys.readouterr().out
    assert main(["program", "--simulate", "--new-addr", "0"]) == 0
    out = capsys.readouterr().out
    assert "SUCCESS" in out and "Lenel OnGuard reader settings" in out
    assert list(tmp_path.rglob("programming_log.csv"))
    assert main(["test-sc", "--simulate"]) == 0
    assert main(["set-key", "--simulate", "--new-key", "random"]) == 0
    assert "KEY:" in capsys.readouterr().out
    assert main(["led", "green", "--simulate"]) == 0
    assert main(["beep", "--simulate", "--key", "default"]) == 0
    assert main(["send", "0x61", "--data", "00", "--simulate"]) == 0
    assert "osdp_PDID" in capsys.readouterr().out


def test_cli_reports_no_response(capsys):
    assert main(["info", "--simulate", "--addr", "9"]) == 2
    assert "No response" in capsys.readouterr().err


def test_cli_commands_and_run(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert main(["commands"]) == 0
    out = capsys.readouterr().out
    assert "[Standard OSDP]" in out and "Buzzer" in out and "--set count=" in out
    assert main(["run", "Buzzer", "--simulate", "--set", "count=2"]) == 0
    assert "ACK" in capsys.readouterr().out
    assert main(["run", "Identity (osdp_ID)", "--simulate"]) == 0
    assert "Serial number" in capsys.readouterr().out
    # settings-changing commands need --yes
    assert main(["run", "comset", "--simulate", "--set", "address=4"]) == 2
    assert main(["run", "comset", "--simulate", "--set", "address=4", "--yes"]) == 0
    assert "address 4" in capsys.readouterr().out
    assert main(["run", "mfg", "--simulate", "--set", "vendor=5A5A5A", "--set", "payload=01", "--yes"]) == 0
    assert "Manufacturer reply" in capsys.readouterr().out
    assert main(["run", "Buzzer", "--simulate", "--set", "bogus=1"]) == 2
    assert main(["send", "0x61", "--data", "00", "--simulate"]) == 0
    assert "Vendor code" in capsys.readouterr().out


def test_cli_vendor_lookup(capsys):
    assert main(["vendor", "00-11-12"]) == 0
    assert "Honeywell CMSS" in capsys.readouterr().out
    assert main(["vendor", "00:06:8e"]) == 0
    assert "HID" in capsys.readouterr().out
    assert main(["vendor", "0011"]) == 2


def test_cli_probe(capsys):
    assert main(["probe", "--simulate"]) == 0
    out = capsys.readouterr().out
    assert "osdp_XWR" in out and "not supported" in out and "Supported: osdp_POLL" in out
    assert main(["probe", "--simulate", "--addr", "9"]) == 2
