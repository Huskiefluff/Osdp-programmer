"""Drive the real GUI in demo mode. Skipped when no display is available."""

import time

import pytest

tk = pytest.importorskip("tkinter")


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available")
    from readerprog import gui

    dialogs = []
    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(gui.messagebox, name, lambda *a, n=name, **k: dialogs.append((n, a)))
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda *a, **k: True)
    root.withdraw()
    application = gui.App(root)
    application.dialogs = dialogs
    application.port_var.set(gui.DEMO_PORT)
    yield application
    application.on_close()


def pump(app, cond, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.root.update()
        if cond():
            return True
        time.sleep(0.02)
    return False


def text(widget):
    return widget.get("1.0", "end")


def test_demo_end_to_end(app):
    # Start from a wrong guess; Find reader must correct it.
    app.addr_var.set(50)
    app.baud_var.set("115200")
    app.find_reader()
    assert pump(app, lambda: "Found reader" in text(app.prog_text)), app.dialogs
    assert app.addr_var.get() == 1 and app.baud_var.get() == "9600"

    app.read_info()
    assert pump(app, lambda: "Capabilities" in text(app.info_text))

    app.new_addr_var.set(0)
    app.new_baud_var.set("9600")
    app.program()
    assert pump(app, lambda: "SUCCESS" in text(app.prog_text)), (text(app.prog_text), app.dialogs)
    assert app.addr_var.get() == 0
    assert "OSDP address                 : 0" in text(app.onguard_text)

    app.verify()
    assert pump(app, lambda: "PASS" in text(app.prog_text)), (text(app.prog_text), app.dialogs)

    app.test_sc()
    assert pump(app, lambda: "Secure channel OK" in text(app.sc_text)), app.dialogs

    app.led("green")
    app.beep()
    app.toggle_monitor()
    assert pump(app, lambda: "Monitoring address 0" in text(app.card_text)), app.dialogs
    from readerprog.sim import h10301_bits

    app.sim_card(h10301_bits(77, 1234))
    assert pump(app, lambda: "Card number: 1234" in text(app.card_text)), text(app.card_text)
    assert "26 bits" in text(app.onguard_text)
    app.toggle_monitor()

    app.raw_code_var.set("61")
    app.raw_data_var.set("00")
    app.send_raw()
    assert pump(app, lambda: "osdp_PDID" in text(app.raw_text))

    app.generate_key()
    app.install_key()
    assert pump(app, lambda: "installed and verified" in text(app.sc_text)), app.dialogs
    assert app.key_mode.get() == "custom"
    assert not [d for d in app.dialogs if d[0] == "showerror"], app.dialogs
    assert "TX ->" in text(app.log_text)


def test_no_reader_reports_clearly(app):
    port = app.port_var.get()  # Tk variables may only be read on the UI thread

    def unplug(s):
        s.open(port, 9600)
        s.sim.powered = False

    app.run("Unplug", unplug)  # jobs run in order, so this happens before the scan
    app.find_reader()
    assert pump(app, lambda: any(d[0] == "showwarning" for d in app.dialogs), timeout=60), app.dialogs
    assert "No reader answered" in text(app.info_text)
