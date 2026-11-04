"""Windows desktop GUI (tkinter) for programming OSDP readers."""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Callable, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import APP_NAME, __version__
from . import programmer as P
from .osdp import constants as C
from .osdp.cp import SCAN_BAUD_ORDER, Bus, OsdpError, Reader, scan
from .osdp.secure import SecureChannelError, parse_key
from .osdp.transport import SerialTransport, list_ports
from .sim import SimulatedReader, SimulatedTransport, fascn_200_bits, h10301_bits

DEMO_PORT = "DEMO - simulated reader (no hardware)"
MONO = ("Consolas", 10) if sys.platform == "win32" else ("TkFixedFont", 10)


class Session:
    """Owns the serial port. Only ever used from the worker thread."""

    def __init__(self, log: Callable[[str, str], None]):
        self.log = log
        self.port: Optional[str] = None
        self.bus: Optional[Bus] = None
        self.sim: Optional[SimulatedReader] = None
        self.monitor: Optional[Reader] = None

    @property
    def is_demo(self) -> bool:
        return self.port == DEMO_PORT

    def open(self, port: str, baud: int) -> Bus:
        if not port:
            raise OsdpError("Select the COM port of the USB-RS485 adapter first.")
        if self.bus is None or port != self.port:
            self.close()
            if port == DEMO_PORT:
                self.sim = SimulatedReader()
                transport = SimulatedTransport([self.sim], baud=baud)
            else:
                transport = SerialTransport(port.split(" ")[0], baud)
            self.port = port
            self.bus = Bus(transport, log=self.log)
            self.log("info", f"Opened {port}")
        self.bus.set_baud(baud)
        return self.bus

    def close(self) -> None:
        self.monitor = None
        if self.bus is not None:
            try:
                self.bus.transport.close()
            except Exception:
                pass
        self.bus = None
        self.port = None
        self.sim = None


class Worker(threading.Thread):
    """Runs hardware jobs one at a time so the window never freezes."""

    def __init__(self, app: "App"):
        super().__init__(daemon=True, name="osdp-worker")
        self.app = app
        self.jobs: "queue.Queue[tuple]" = queue.Queue()
        self.monitor_enabled = threading.Event()
        self.cancel = threading.Event()
        self.stopping = threading.Event()

    def submit(self, label: str, fn: Callable[[Session], object],
               done: Optional[Callable[[object], None]] = None) -> None:
        self.jobs.put((label, fn, done))

    def run(self) -> None:
        session = self.app.session
        while not self.stopping.is_set():
            try:
                label, fn, done = self.jobs.get(timeout=0.15)
            except queue.Empty:
                if self.monitor_enabled.is_set() and session.monitor is not None:
                    self._monitor_poll(session)
                continue
            if label is None:
                break
            self.cancel.clear()
            self.app.post(lambda l=label: self.app.set_busy(l))
            try:
                result = fn(session)
            except (OsdpError, SecureChannelError, ValueError) as exc:
                self.app.post(lambda e=exc, l=label: self.app.job_failed(l, str(e)))
            except OSError as exc:
                self.app.post(lambda e=exc: self.app.job_failed("Port error", f"Could not use the port: {e}"))
                session.close()
            except Exception as exc:  # pragma: no cover - defensive
                tb = traceback.format_exc()
                self.app.post(lambda e=exc, t=tb: self.app.job_failed("Unexpected error", f"{e}\n\n{t}"))
            else:
                if done is not None:
                    self.app.post(lambda r=result, d=done: d(r))
            finally:
                self.app.post(self.app.set_idle)
        session.close()

    def _monitor_poll(self, session: Session) -> None:
        try:
            events = session.monitor.poll()  # type: ignore[union-attr]
        except Exception as exc:
            self.app.post(lambda e=exc: self.app.monitor_error(str(e)))
            return
        for ev in events:
            if ev.kind == "card":
                try:
                    session.monitor.buzzer(count=1, on_time=2, off_time=1)  # type: ignore[union-attr]
                    session.monitor.led(temp_on_color=2, temp_timer=10)  # type: ignore[union-attr]
                except Exception:
                    pass
            self.app.post(lambda e=ev: self.app.show_event(e))


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.ui_queue: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self.session = Session(self._log_from_worker)
        self.worker = Worker(self)
        self.last_device = None
        self.last_card_bits: Optional[int] = None
        self.installed_custom_key = False

        root.title(f"{APP_NAME} {__version__} - OSDP readers for Lenel OnGuard")
        root.geometry("1040x760")
        root.minsize(900, 640)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_connection()
        self._build_tabs()
        self._build_status()
        self.refresh_ports()
        self.update_warnings()
        self.worker.start()
        self._after_id: Optional[str] = self.root.after(50, self._drain_ui_queue)

    # ------------------------------------------------------------------
    # thread plumbing
    # ------------------------------------------------------------------
    def post(self, fn: Callable[[], None]) -> None:
        self.ui_queue.put(fn)

    def _drain_ui_queue(self) -> None:
        try:
            while True:
                self.ui_queue.get_nowait()()
        except queue.Empty:
            pass
        try:
            self._after_id = self.root.after(50, self._drain_ui_queue)
        except tk.TclError:
            self._after_id = None  # window closed

    def _log_from_worker(self, kind: str, text: str) -> None:
        self.post(lambda: self.append_log(kind, text))

    def run(self, label: str, fn: Callable[[Session], object], done: Optional[Callable[[object], None]] = None):
        self.worker.submit(label, fn, done)

    # ------------------------------------------------------------------
    # layout
    # ------------------------------------------------------------------
    def _build_connection(self) -> None:
        frm = ttk.LabelFrame(self.root, text="Connection (USB-RS485 adapter -> reader)", padding=8)
        frm.pack(fill="x", padx=10, pady=(10, 4))

        ttk.Label(frm, text="COM port:").grid(row=0, column=0, sticky="w")
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(frm, textvariable=self.port_var, width=58, state="readonly")
        self.port_combo.grid(row=0, column=1, columnspan=3, sticky="we", padx=4)
        ttk.Button(frm, text="Refresh", command=self.refresh_ports).grid(row=0, column=4, padx=4)

        ttk.Label(frm, text="Reader address:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.addr_var = tk.IntVar(value=P.VERIDT_DEFAULT_ADDRESS)
        ttk.Spinbox(frm, from_=0, to=126, textvariable=self.addr_var, width=6,
                    command=self.update_warnings).grid(row=1, column=1, sticky="w", padx=4, pady=(6, 0))
        ttk.Label(frm, text="Baud:").grid(row=1, column=2, sticky="e", pady=(6, 0))
        self.baud_var = tk.StringVar(value=str(P.VERIDT_DEFAULT_BAUD))
        ttk.Combobox(frm, textvariable=self.baud_var, values=[str(b) for b in C.BAUD_RATES], width=8,
                     state="readonly").grid(row=1, column=3, sticky="w", padx=4, pady=(6, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=1, column=4, columnspan=2, sticky="e", pady=(6, 0))
        ttk.Button(btns, text="Find reader", command=self.find_reader).pack(side="left", padx=2)
        ttk.Button(btns, text="Read info", command=self.read_info).pack(side="left", padx=2)
        ttk.Button(btns, text="Stop", command=self.cancel_job).pack(side="left", padx=2)
        ttk.Label(frm, text="Factory default for Veridt readers is address 1 @ 9600. "
                            "'Find reader' detects the current address and baud automatically.",
                  foreground="#555").grid(row=2, column=0, columnspan=6, sticky="w", pady=(6, 0))
        frm.columnconfigure(1, weight=1)

    def _build_tabs(self) -> None:
        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill="both", expand=True, padx=10, pady=4)
        self._build_program_tab()
        self._build_secure_tab()
        self._build_test_tab()
        self._build_info_tab()
        self._build_advanced_tab()
        self._build_log_tab()
        self._build_help_tab()

    def _text(self, parent, height=10, **kw) -> tk.Text:
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, **kw)
        text = tk.Text(frame, height=height, wrap="word", font=MONO, relief="solid", borderwidth=1)
        sb = ttk.Scrollbar(frame, command=text.yview)
        text.configure(yscrollcommand=sb.set)
        text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        text.tag_configure("ok", foreground="#0a7a0a")
        text.tag_configure("err", foreground="#b00020")
        text.tag_configure("warn", foreground="#a05a00")
        text.tag_configure("tx", foreground="#1f4e9a")
        text.tag_configure("rx", foreground="#6a1b9a")
        return text

    def _build_program_tab(self) -> None:
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text="1. Program address / baud")

        top = ttk.Frame(tab)
        top.pack(fill="x")
        ttk.Label(top, text="Preset:").grid(row=0, column=0, sticky="w")
        self.preset_var = tk.StringVar(value=P.PRESETS[0].label)
        preset = ttk.Combobox(top, textvariable=self.preset_var, values=[p.label for p in P.PRESETS],
                              width=52, state="readonly")
        preset.grid(row=0, column=1, columnspan=3, sticky="w", padx=4)
        preset.bind("<<ComboboxSelected>>", lambda e: self.apply_preset())

        ttk.Label(top, text="New address:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.new_addr_var = tk.IntVar(value=P.PRESETS[0].address)
        ttk.Spinbox(top, from_=0, to=126, textvariable=self.new_addr_var, width=6,
                    command=self.update_warnings).grid(row=1, column=1, sticky="w", padx=4, pady=(8, 0))
        ttk.Label(top, text="New baud:").grid(row=1, column=2, sticky="e", pady=(8, 0))
        self.new_baud_var = tk.StringVar(value=str(P.PRESETS[0].baud))
        nb = ttk.Combobox(top, textvariable=self.new_baud_var, values=[str(b) for b in C.BAUD_RATES],
                          width=8, state="readonly")
        nb.grid(row=1, column=3, sticky="w", padx=4, pady=(8, 0))
        nb.bind("<<ComboboxSelected>>", lambda e: self.update_warnings())

        ttk.Label(top, text="Door / label:").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.door_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.door_var, width=40).grid(row=2, column=1, columnspan=3, sticky="w",
                                                                padx=4, pady=(8, 0))
        ttk.Label(top, text="optional, saved in the log", foreground="#555").grid(
            row=2, column=4, sticky="w", pady=(8, 0))

        for var in (self.addr_var, self.new_addr_var, self.door_var):
            var.trace_add("write", lambda *a: self.update_warnings())

        self.prog_secure_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Send the change over secure channel (key from the Secure channel tab)",
                        variable=self.prog_secure_var).grid(row=3, column=0, columnspan=4, sticky="w", pady=(8, 0))

        actions = ttk.Frame(tab)
        actions.pack(fill="x", pady=8)
        ttk.Button(actions, text="Program reader", command=self.program, style="Accent.TButton").pack(side="left")
        ttk.Button(actions, text="Verify after power cycle", command=self.verify).pack(side="left", padx=6)
        ttk.Button(actions, text="Find + program next reader...", command=self.program_next).pack(side="left")

        self.warn_label = ttk.Label(tab, text="", foreground="#a05a00", wraplength=960, justify="left")
        self.warn_label.pack(fill="x")

        panes = ttk.Panedwindow(tab, orient="horizontal")
        panes.pack(fill="both", expand=True, pady=(6, 0))
        left = ttk.LabelFrame(panes, text="Result", padding=4)
        right = ttk.LabelFrame(panes, text="Enter these settings in OnGuard", padding=4)
        panes.add(left, weight=1)
        panes.add(right, weight=1)
        self.prog_text = self._text(left)
        self.onguard_text = self._text(right)
        ttk.Button(right, text="Copy to clipboard", command=self.copy_onguard).pack(anchor="e", pady=(4, 0))
        self.refresh_onguard()

    def _build_secure_tab(self) -> None:
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text="2. Secure channel")
        ttk.Label(tab, wraplength=960, justify="left", text=(
            "OSDP Secure Channel encrypts the reader <-> panel link with AES-128. A new reader is in "
            "'install mode' and accepts the default key (SCBK-D). With Lenel OnGuard you normally leave the "
            "reader in install mode and let the panel install its own key when you enable encryption for the "
            "reader. Install a custom key here only if your OnGuard version lets you enter that same key. "
            "Once a reader has a custom key you need that key to change its settings again.")).pack(fill="x")

        cur = ttk.LabelFrame(tab, text="Key the reader currently uses", padding=8)
        cur.pack(fill="x", pady=8)
        self.key_mode = tk.StringVar(value="default")
        ttk.Radiobutton(cur, text="Default install key (SCBK-D, factory)", value="default",
                        variable=self.key_mode).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(cur, text="Custom key (32 hex digits):", value="custom",
                        variable=self.key_mode).grid(row=1, column=0, sticky="w")
        self.cur_key_var = tk.StringVar()
        ttk.Entry(cur, textvariable=self.cur_key_var, width=48, font=MONO).grid(row=1, column=1, sticky="w", padx=4)
        ttk.Button(cur, text="Test secure channel", command=self.test_sc).grid(row=0, column=2, rowspan=2, padx=8)

        new = ttk.LabelFrame(tab, text="Install a new key", padding=8)
        new.pack(fill="x")
        self.new_key_var = tk.StringVar()
        ttk.Label(new, text="New key:").grid(row=0, column=0, sticky="w")
        ttk.Entry(new, textvariable=self.new_key_var, width=48, font=MONO).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Button(new, text="Generate random", command=self.generate_key).grid(row=0, column=2, padx=4)
        ttk.Button(new, text="Install key", command=self.install_key).grid(row=0, column=3, padx=4)
        self.log_key_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(new, text="Record the installed key in the programming log CSV "
                                  "(keep that file secure)", variable=self.log_key_var).grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))
        self.sc_text = self._text(tab, height=8, pady=(8, 0))

    def _build_test_tab(self) -> None:
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text="3. Test LED / buzzer / cards")
        row = ttk.Frame(tab)
        row.pack(fill="x")
        ttk.Label(row, text="LED:").pack(side="left")
        for name in ("green", "red", "amber", "blue", "white", "off"):
            ttk.Button(row, text=name.title(), width=7,
                       command=lambda n=name: self.led(n)).pack(side="left", padx=2)
        ttk.Button(row, text="Beep", command=self.beep).pack(side="left", padx=(16, 2))

        mon = ttk.Frame(tab)
        mon.pack(fill="x", pady=8)
        self.monitor_btn = ttk.Button(mon, text="Start card monitor", command=self.toggle_monitor)
        self.monitor_btn.pack(side="left")
        ttk.Label(mon, text="  Present a card; bit length and decoded fields appear below. "
                            "Use the bit length to choose the card format in OnGuard.").pack(side="left")
        self.demo_cards = ttk.Frame(tab)
        self.demo_cards.pack(fill="x")
        ttk.Label(self.demo_cards, text="Demo mode - simulate a card:").pack(side="left")
        ttk.Button(self.demo_cards, text="26-bit H10301",
                   command=lambda: self.sim_card(h10301_bits(123, 4567))).pack(side="left", padx=2)
        ttk.Button(self.demo_cards, text="200-bit FASC-N (PIV)",
                   command=lambda: self.sim_card(fascn_200_bits("9999", "1234", "123456")))\
            .pack(side="left", padx=2)
        self.card_text = self._text(tab, pady=(8, 0))

    def _build_info_tab(self) -> None:
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text="Reader info")
        self.info_text = self._text(tab)

    def _build_advanced_tab(self) -> None:
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text="Advanced")
        ttk.Label(tab, wraplength=960, justify="left", text=(
            "Send any OSDP command to the reader. Use this only with guidance from the reader manufacturer "
            "(for example an osdp_MFG 0x80 command whose payload starts with the 3-byte vendor code).")).pack(fill="x")
        row = ttk.Frame(tab)
        row.pack(fill="x", pady=8)
        ttk.Label(row, text="Command code (hex):").pack(side="left")
        self.raw_code_var = tk.StringVar(value="61")
        ttk.Entry(row, textvariable=self.raw_code_var, width=6, font=MONO).pack(side="left", padx=4)
        ttk.Label(row, text="Data (hex):").pack(side="left")
        self.raw_data_var = tk.StringVar(value="00")
        ttk.Entry(row, textvariable=self.raw_data_var, width=60, font=MONO).pack(side="left", padx=4)
        self.raw_secure_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="secure channel", variable=self.raw_secure_var).pack(side="left")
        ttk.Button(row, text="Send", command=self.send_raw).pack(side="left", padx=4)
        self.raw_text = self._text(tab)

    def _build_log_tab(self) -> None:
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text="Log")
        row = ttk.Frame(tab)
        row.pack(fill="x")
        self.show_packets = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="Show packet trace", variable=self.show_packets).pack(side="left")
        ttk.Button(row, text="Clear", command=lambda: self.log_text.delete("1.0", "end")).pack(side="left", padx=4)
        ttk.Button(row, text="Save log...", command=self.save_log).pack(side="left", padx=4)
        ttk.Button(row, text="Open programming log folder", command=self.open_log_folder).pack(side="left", padx=4)
        self.log_text = self._text(tab, pady=(8, 0))

    def _build_help_tab(self) -> None:
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text="Help")
        text = self._text(tab)
        text.insert("end", HELP_TEXT + "\n\nVeridt field notes\n------------------\n")
        for note in P.VERIDT_NOTES:
            text.insert("end", f"* {note}\n")
        text.configure(state="disabled")

    def _build_status(self) -> None:
        bar = ttk.Frame(self.root, padding=(10, 2))
        bar.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(bar, textvariable=self.status_var).pack(side="left")
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=180)
        self.progress.pack(side="right")

    # ------------------------------------------------------------------
    # small helpers
    # ------------------------------------------------------------------
    def set_busy(self, label: str) -> None:
        self.status_var.set(f"{label}...")
        self.progress.start(12)

    def set_idle(self) -> None:
        if self.worker.jobs.empty():
            self.progress.stop()
            self.progress["value"] = 0
            if self.status_var.get().endswith("..."):
                self.status_var.set("Ready")

    def job_failed(self, label: str, message: str) -> None:
        self.status_var.set(f"{label} failed")
        self.append_log("error", f"{label}: {message}")
        hint = ""
        if "No response" in message:
            hint = ("\n\nChecks: reader has 12 VDC power; RS-485 A/B (yellow/blue) not swapped; "
                    "correct COM port; address/baud correct (try 'Find reader').")
        elif "Encrypted communication required" in message:
            hint = "\n\nThe reader has a secure channel key installed. Enter it on the Secure channel tab."
        messagebox.showerror(APP_NAME, f"{label} failed:\n\n{message}{hint}")

    def append_log(self, kind: str, text: str) -> None:
        if kind in ("tx", "rx") and not self.show_packets.get():
            return
        stamp = time.strftime("%H:%M:%S")
        prefix = {"tx": "TX ->", "rx": "RX <-", "info": "     ", "error": "ERR  "}.get(kind, kind)
        tag = {"tx": "tx", "rx": "rx", "error": "err"}.get(kind, "")
        self.log_text.insert("end", f"{stamp} {prefix} {text}\n", tag)
        self.log_text.see("end")

    @staticmethod
    def put(widget: tk.Text, text: str, tag: str = "", clear: bool = False) -> None:
        if clear:
            widget.delete("1.0", "end")
        widget.insert("end", text + "\n", tag)
        widget.see("end")

    def _port(self) -> str:
        return self.port_var.get()

    def _addr(self) -> int:
        value = int(self.addr_var.get())
        if not 0 <= value <= 126:
            raise ValueError("Reader address must be 0-126")
        return value

    def _baud(self) -> int:
        return int(self.baud_var.get())

    def _current_key(self) -> Optional[bytes]:
        if self.key_mode.get() == "default":
            return None
        return parse_key(self.cur_key_var.get())

    def _guard(self, fn: Callable[[], None]) -> None:
        try:
            fn()
        except (ValueError, tk.TclError) as exc:
            messagebox.showerror(APP_NAME, str(exc))

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------
    def refresh_ports(self) -> None:
        try:
            ports = list_ports()
        except Exception as exc:  # pragma: no cover
            ports = []
            self.append_log("error", f"Could not list ports: {exc}")
        labels = [p.label() + ("   <- USB-RS485?" if p.likely_rs485_adapter else "") for p in ports]
        labels.append(DEMO_PORT)
        self.port_combo["values"] = labels
        if self.port_var.get() not in labels:
            self.port_var.set(labels[0])
        self.status_var.set(f"{len(ports)} serial port(s) found")

    def apply_preset(self) -> None:
        for p in P.PRESETS:
            if p.label == self.preset_var.get():
                self.new_addr_var.set(p.address)
                self.new_baud_var.set(str(p.baud))
        self.update_warnings()

    def update_warnings(self) -> None:
        try:
            warnings = P.comset_warnings(int(self.addr_var.get()), int(self.new_addr_var.get()),
                                         int(self.new_baud_var.get()))
        except (tk.TclError, ValueError, AttributeError):
            return
        if hasattr(self, "warn_label"):
            self.warn_label.configure(text="\n".join(f"! {w}" for w in warnings))
            self.refresh_onguard()

    def refresh_onguard(self) -> None:
        try:
            addr, baud = int(self.new_addr_var.get()), int(self.new_baud_var.get())
        except (tk.TclError, ValueError):
            return
        mode = "custom" if self.installed_custom_key else "plain"
        text = P.onguard_checklist(addr, baud, device_id=self.last_device, secure_mode=mode,
                                   card_bits=self.last_card_bits, door=self.door_var.get().strip())
        self.onguard_text.delete("1.0", "end")
        self.onguard_text.insert("end", text)

    def copy_onguard(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.onguard_text.get("1.0", "end").strip())
        self.status_var.set("OnGuard settings copied to clipboard")

    def cancel_job(self) -> None:
        self.worker.cancel.set()
        self.status_var.set("Stopping...")

    def find_reader(self, then: Optional[Callable[[], None]] = None) -> None:
        port, baud = self._port(), self._baud()

        def job(s: Session):
            bus = s.open(port, baud)
            return scan(bus, bauds=SCAN_BAUD_ORDER,
                        progress=lambda text, frac: self.post(lambda: self.status_var.set(text + "...")),
                        cancelled=self.worker.cancel.is_set)

        def done(found):
            if not found:
                self.put(self.info_text, "No reader answered at any address or baud rate.\n"
                         "Check the 12 VDC supply, RS-485 A/B polarity and the COM port.", "err", clear=True)
                self.tabs.select(3)
                messagebox.showwarning(APP_NAME, "No reader found. Check power, wiring and the COM port.")
                return
            f = found[0]
            self.addr_var.set(f.address)
            self.baud_var.set(str(f.baud))
            self.last_device = f.device_id
            self.put(self.info_text, f.describe(), "ok", clear=True)
            self.put(self.prog_text, f"Found reader at address {f.address} @ {f.baud} baud.", "ok", clear=True)
            self.status_var.set(f"Reader found: address {f.address} @ {f.baud}")
            self.update_warnings()
            if then is not None:
                then()

        self.run("Searching for reader", job, done)

    def read_info(self) -> None:
        def go():
            port, addr, baud = self._port(), self._addr(), self._baud()

            def job(s: Session):
                return P.read_info(s.open(port, baud), addr, baud)

            def done(info):
                self.last_device = info.device_id
                self.put(self.info_text, info.describe(), clear=True)
                self.tabs.select(3)
                self.refresh_onguard()

            self.run("Reading reader info", job, done)
        self._guard(go)

    def program(self, after: Optional[Callable[[], None]] = None) -> None:
        def go():
            port, addr, baud = self._port(), self._addr(), self._baud()
            new_addr, new_baud = int(self.new_addr_var.get()), int(self.new_baud_var.get())
            door = self.door_var.get().strip()
            use_sc = self.prog_secure_var.get()
            key = self._current_key() if use_sc else None
            if not 0 <= new_addr <= 126:
                raise ValueError("New address must be 0-126")

            def job(s: Session):
                return P.program_reader(s.open(port, baud), addr, baud, new_addr, new_baud,
                                        key=key, use_secure=use_sc)

            def done(res: P.ProgramResult):
                tag = "ok" if res.ok and res.verified else ("warn" if res.ok else "err")
                self.put(self.prog_text, res.describe(), tag, clear=True)
                if res.ok:
                    self.addr_var.set(res.new_address)
                    self.baud_var.set(str(res.new_baud))
                    self.last_device = res.device_id or self.last_device
                    self.put(self.prog_text, "\nNext: remove power from the reader for a few seconds, restore it, "
                             "then click 'Verify after power cycle'.")
                    dev = res.device_id
                    try:
                        path = P.append_log({
                            "port": port, "action": "program",
                            "serial_number": dev.serial_number if dev else "",
                            "vendor_code": dev.vendor_hex if dev else "", "model": dev.model if dev else "",
                            "firmware": dev.firmware if dev else "", "old_address": addr, "old_baud": baud,
                            "new_address": res.new_address, "new_baud": res.new_baud,
                            "verified": res.verified, "notes": " | ".join(([door] if door else []) + res.warnings)})
                        self.append_log("info", f"Programming logged to {path}")
                    except OSError as exc:
                        self.append_log("error", f"Could not write programming log: {exc}")
                self.refresh_onguard()
                if after:
                    after()

            self.run("Programming reader", job, done)
        self._guard(go)

    def program_next(self) -> None:
        if messagebox.askokcancel(APP_NAME, "Connect the next reader (one reader on the adapter at a time), "
                                            "then click OK to find and program it with the settings above."):
            self.find_reader(then=self.program)

    def verify(self) -> None:
        def go():
            port, addr, baud = self._port(), self._addr(), self._baud()
            serial = self.last_device.serial_number if self.last_device else None
            if port != DEMO_PORT and not messagebox.askokcancel(
                    APP_NAME, "Remove power from the reader for about 5 seconds, restore it, wait until it "
                              "finishes starting up, then click OK."):
                return

            def job(s: Session):
                bus = s.open(port, baud)
                if s.is_demo and s.sim is not None:
                    s.sim.power_cycle()
                    self._log_from_worker("info", "Demo: simulated reader power cycled")
                return P.verify_after_power_cycle(bus, addr, baud, serial,
                                                  progress=lambda t, f: self.post(lambda: self.status_var.set(t)),
                                                  cancelled=self.worker.cancel.is_set)

            def done(res: P.VerifyResult):
                self.put(self.prog_text, "\n" + res.text, "ok" if res.ok else "err")
                if res.found and not res.ok:
                    self.addr_var.set(res.found.address)
                    self.baud_var.set(str(res.found.baud))
                if res.ok:
                    messagebox.showinfo(APP_NAME, res.text)
                    try:
                        dev = res.found.device_id if res.found else None
                        P.append_log({"port": port, "action": "verify", "serial_number": dev.serial_number if dev else "",
                                      "new_address": addr, "new_baud": baud, "verified": True})
                    except OSError:
                        pass
                else:
                    messagebox.showwarning(APP_NAME, res.text)

            self.run("Verifying after power cycle", job, done)
        self._guard(go)

    def test_sc(self) -> None:
        def go():
            port, addr, baud, key = self._port(), self._addr(), self._baud(), self._current_key()
            self.run("Testing secure channel", lambda s: P.test_secure_channel(s.open(port, baud), addr, baud, key),
                     lambda msg: self.put(self.sc_text, msg, "ok"))
        self._guard(go)

    def generate_key(self) -> None:
        self.new_key_var.set(os.urandom(16).hex().upper())

    def install_key(self) -> None:
        def go():
            port, addr, baud, key = self._port(), self._addr(), self._baud(), self._current_key()
            new_key = parse_key(self.new_key_var.get())
            if not messagebox.askokcancel(
                    APP_NAME, "Install this key in the reader?\n\n"
                              f"{new_key.hex().upper()}\n\nWrite it down. Without it you cannot change the "
                              "reader's secure settings again, and OnGuard must be set to the same key."):
                return

            def done(msg):
                self.put(self.sc_text, msg, "ok")
                self.installed_custom_key = True
                self.key_mode.set("custom")
                self.cur_key_var.set(new_key.hex().upper())
                dev = self.last_device
                try:
                    P.append_log({"port": port, "action": "install key",
                                  "serial_number": dev.serial_number if dev else "", "new_address": addr,
                                  "new_baud": baud, "verified": True,
                                  "key": new_key.hex().upper() if self.log_key_var.get()
                                  else f"fingerprint {P.key_fingerprint(new_key)}"})
                except OSError as exc:
                    self.append_log("error", f"Could not write programming log: {exc}")
                self.refresh_onguard()

            self.run("Installing key", lambda s: P.install_key(s.open(port, baud), addr, baud, new_key, key), done)
        self._guard(go)

    def _with_reader(self, label: str, action: Callable[[Reader], object], done=None, secure: bool = False):
        def go():
            port, addr, baud = self._port(), self._addr(), self._baud()
            key = self._current_key() if secure else None

            def job(s: Session):
                bus = s.open(port, baud)
                if s.monitor is not None and s.monitor.address == addr:
                    reader = s.monitor
                else:
                    reader = Reader(bus, addr)
                    if secure:
                        reader.start_secure_channel(key)
                return action(reader)

            self.run(label, job, done)
        self._guard(go)

    def led(self, color: str) -> None:
        value = C.LED_COLOR_BY_NAME[color]
        if color == "off":
            self._with_reader("LED off", lambda r: r.led(perm_on_color=0, cancel_temp=True))
        else:
            self._with_reader(f"LED {color}", lambda r: r.led(temp_on_color=value, temp_timer=30))

    def beep(self) -> None:
        self._with_reader("Beep", lambda r: r.buzzer(count=2, on_time=2, off_time=2))

    def toggle_monitor(self) -> None:
        if self.worker.monitor_enabled.is_set():
            self.worker.monitor_enabled.clear()
            self.monitor_btn.configure(text="Start card monitor")
            self.status_var.set("Card monitor stopped")
            return

        def go():
            port, addr, baud = self._port(), self._addr(), self._baud()

            def job(s: Session):
                bus = s.open(port, baud)
                reader = Reader(bus, addr)
                reader.poll()
                s.monitor = reader
                return reader

            def done(_):
                self.worker.monitor_enabled.set()
                self.monitor_btn.configure(text="Stop card monitor")
                self.put(self.card_text, f"Monitoring address {addr} @ {baud}. Present a card...")
                self.status_var.set("Card monitor running")

            self.run("Starting card monitor", job, done)
        self._guard(go)

    def monitor_error(self, message: str) -> None:
        self.worker.monitor_enabled.clear()
        self.monitor_btn.configure(text="Start card monitor")
        self.put(self.card_text, f"Monitor stopped: {message}", "err")

    def show_event(self, ev) -> None:
        self.put(self.card_text, ev.text, "ok" if ev.kind == "card" else "")
        if ev.card is not None and ev.card.bit_count:
            self.last_card_bits = ev.card.bit_count
            self.refresh_onguard()

    def sim_card(self, bits: str) -> None:
        if self._port() != DEMO_PORT:
            messagebox.showinfo(APP_NAME, "Simulated cards only work with the DEMO port selected.")
            return

        baud = self._baud()

        def job(s: Session):
            s.open(DEMO_PORT, baud)
            if s.sim is None:
                raise OsdpError("Demo reader not available")
            s.sim.present_card(bits)

        self.run("Presenting simulated card", job)

    def send_raw(self) -> None:
        def go():
            code = int(self.raw_code_var.get(), 16)
            data = bytes.fromhex(self.raw_data_var.get().replace(" ", ""))

            def action(r: Reader):
                return r.command(code, data, raise_nak=False)

            def done(reply):
                self.put(self.raw_text, f"> {C.command_name(code)} {data.hex(' ').upper()}")
                self.put(self.raw_text, f"< {reply.name} {reply.data.hex(' ').upper()}",
                         "err" if reply.code == C.REPLY_NAK else "ok")

            self._with_reader("Sending command", action, done, secure=self.raw_secure_var.get())
        self._guard(go)

    def save_log(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text", "*.txt")],
                                            initialfile=time.strftime("osdp-log-%Y%m%d-%H%M%S.txt"))
        if path:
            Path(path).write_text(self.log_text.get("1.0", "end"), encoding="utf-8")
            self.status_var.set(f"Log saved to {path}")

    def open_log_folder(self) -> None:
        folder = P.default_log_dir()
        folder.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(folder)  # type: ignore[attr-defined]
        else:
            messagebox.showinfo(APP_NAME, str(folder))

    def on_close(self) -> None:
        self.worker.monitor_enabled.clear()
        self.worker.stopping.set()
        self.worker.jobs.put((None, None, None))
        self.worker.join(timeout=2)
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        self.root.destroy()


HELP_TEXT = """Quick start
-----------
1. Wire the reader to the USB-RS485 adapter: reader RS-485 + (A) to adapter A/D+, RS-485 - (B) to
   adapter B/D-, and connect grounds. Power the reader from a separate 12 VDC supply (300 mA+).
   Leave the tamper wire disconnected. Connect ONE reader at a time.
2. Pick the adapter's COM port above (Device Manager > Ports shows it) and click 'Find reader'.
   The tool tries every baud rate and address and fills in the current settings.
3. On '1. Program', choose the address and baud rate that you will enter in OnGuard and click
   'Program reader'. The tool confirms the reader answers at the new settings.
4. Power-cycle the reader and click 'Verify after power cycle' to prove it kept the settings.
5. Copy the 'Enter these settings in OnGuard' panel into the reader's properties in OnGuard.
6. Optional: '3. Test' checks the LED, buzzer and card reads (shows card bit length for the
   OnGuard card format).

Every programming action is appended to programming_log.csv in Documents\\OSDP Reader Programmer.

If nothing answers: check power, swap A/B, confirm the COM port, make sure no panel is connected
to the same wires, and try 'Find reader' again."""


def _enable_dpi_awareness() -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
        except Exception:
            pass


def main() -> None:
    _enable_dpi_awareness()
    root = tk.Tk()
    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
