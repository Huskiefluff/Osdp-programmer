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


def _select(app, name):
    for iid, (kind, obj) in app._tree_items.items():
        if kind == "command" and obj[0].name == name:
            app.cmd_tree.selection_set(iid)
            app.root.update()
            return
    raise AssertionError(f"{name} not in command tree")


def test_commands_tab(app, tmp_path, monkeypatch):
    from readerprog import gui
    from readerprog.commands import CommandLibrary, UserCommand
    from readerprog.sim import SIM_VENDOR

    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **k: True)

    _select(app, "Buzzer")
    app.param_vars["count"].set("2")
    app.root.update()
    assert "osdp_BUZ" in app.cmd_preview.cget("text")
    app.send_selected_command()
    assert pump(app, lambda: "ACK" in text(app.cmd_text)), text(app.cmd_text)
    app.param_vars["count"].set("999")
    app.root.update()
    assert "Fix this first" in app.cmd_preview.cget("text")

    _select(app, "Manufacturer command (osdp_MFG)")
    app.param_vars["vendor"].set(SIM_VENDOR.hex())
    app.param_vars["payload"].set("AA")
    app.send_selected_command()
    assert pump(app, lambda: "Manufacturer reply" in text(app.cmd_text)), text(app.cmd_text)

    # Add, run, edit, export, delete and re-import a user command.
    uc = UserCommand(name="Sim echo", vendor="Veridt", code="80", data=SIM_VENDOR.hex() + " 01", notes="test")
    app._ask_user_command = lambda existing, vendor="Other": uc
    app.new_user_command()
    lib_path = CommandLibrary.default_path()
    assert lib_path.exists() and "Sim echo" in lib_path.read_text()
    _select(app, "Sim echo")
    assert app.selected_user == uc
    app.send_selected_command()
    assert pump(app, lambda: text(app.cmd_text).count("Manufacturer reply") >= 2), text(app.cmd_text)

    edited = UserCommand(name="Sim echo 2", vendor="Veridt", code="80", data=SIM_VENDOR.hex() + " 02")
    app._ask_user_command = lambda existing, vendor="Other": edited
    app.edit_user_command()
    assert [c.name for c in app.library.commands] == ["Sim echo 2"]

    export = tmp_path / "shared.json"
    monkeypatch.setattr(gui.filedialog, "asksaveasfilename", lambda **k: str(export))
    app.export_library()
    assert export.exists()
    _select(app, "Sim echo 2")
    app.delete_user_command()
    assert app.library.commands == []
    monkeypatch.setattr(gui.filedialog, "askopenfilename", lambda **k: str(export))
    app.import_library()
    assert [c.name for c in app.library.commands] == ["Sim echo 2"]
    assert not [d for d in app.dialogs if d[0] == "showerror"], app.dialogs

    # The add/edit dialog validates before saving.
    dlg = gui.UserCommandDialog(app.root, None, vendor="HID", vendor_code="00 06 8E", modal=False)
    dlg.name_var.set("Bad")
    dlg.code_var.set("12")
    dlg.save()
    assert dlg.result is None and any(d[0] == "showerror" for d in app.dialogs)
    dlg.code_var.set("80")
    dlg.save()
    assert dlg.result is not None and dlg.result.vendor == "HID" and dlg.result.data.startswith("00 06 8E")
