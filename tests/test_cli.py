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
