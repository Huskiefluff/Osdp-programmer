# OSDP Reader Programmer

A Windows program that finds, programs and tests **OSDP access-control readers** so they come online
cleanly in **Lenel OnGuard**. It was built for **Veridt Stealth Lite 900W2037** readers connected
through an **Accu-Tech 662314 USB-to-RS-485 adapter**, and works with any reader that speaks
standard SIA OSDP (v2.1.7 / 2.2).

What it does:

| Task | How |
| --- | --- |
| Find a reader | Tries every baud rate (9600 to 230400) and every address (0 to 126), then shows what it found |
| Read the reader | Vendor code, model, OSDP serial number, firmware, capabilities, tamper and power status |
| Program it for OnGuard | Sets the reader's OSDP address and baud rate (`osdp_COMSET`) and checks it answers at the new settings |
| Prove it survives power loss | "Verify after power cycle" checks the reader kept its settings, and finds it again if it didn't |
| Secure channel | Tests AES-128 secure channel with the default install key or a custom key, and can install a new key (`osdp_KEYSET`) |
| Test before you mount it | LED colors, buzzer, live card reads with bit length and decoded fields (26-bit, 37-bit, 75-bit PIV, 200-bit FASC-N) |
| Tell you what to enter in OnGuard | Generates an OnGuard settings checklist you can copy |
| Keep records | Appends every action to `Documents\OSDP Reader Programmer\programming_log.csv` |
| Try it without hardware | A **DEMO** port simulates a Veridt-style reader |

It has a GUI (`OSDPReaderProgrammer.exe`) and a command-line version (`readerprog.exe`) for scripting.

---

## 1. Get the program

**Option A: download the built .exe (no Python needed)**
1. Open this repository's **Actions** tab and select the latest green **OSDP Reader Programmer** run.
2. Download the **OSDPReaderProgrammer-windows** artifact and unzip it.
3. Run `OSDPReaderProgrammer.exe`. Windows SmartScreen may warn because the file isn't code-signed.
   Choose *More info → Run anyway*.

**Option B: build it yourself on Windows**
1. Install Python 3.9 or newer from <https://www.python.org/downloads/windows/>, and tick
   *Add python.exe to PATH*.
2. Double-click `build_exe.bat`. It creates a virtual environment, runs the tests and writes
   `dist\OSDPReaderProgrammer.exe` and `dist\readerprog.exe`.

**Option C: run from source**: double-click `run.bat`.

## 2. Bench wiring

```
   Accu-Tech 662314                              Veridt 900W2037 pigtail
   USB-RS485 adapter                             (confirm colors against the reader's label)
  ┌──────────────┐                              ┌───────────────────────────┐
  │ A / D+ / 485+├──────────────────────────────┤ Yellow  RS-485 +  (A)     │
  │ B / D- / 485-├──────────────────────────────┤ Blue    RS-485 -  (B)     │
  │ GND          ├──────────────┬───────────────┤ Black   Ground            │
  └──────┬───────┘              │               │ Red     +12 VDC           │
         │ USB                  │               │ Orange  Tamper (not used) │
      Windows PC        12 VDC supply (-) ──────┘└────────────┬──────────────┘
                        12 VDC supply (+) ────────────────────┘
```

* Power the reader from a **12 VDC (±1 V) supply rated for at least 300 mA**. The USB adapter
  doesn't power the reader. Veridt warns that higher voltage can damage it.
* Connect **one reader at a time** while programming. Every new Veridt reader ships at the same
  address, so two on the bus will collide.
* Leave the **tamper wire disconnected**; Veridt says it isn't used in OSDP mode.
* Disconnect the panel from the same wires while programming; two masters on one RS-485 line won't work.
* Windows normally installs the adapter's driver automatically (FTDI, CP210x, CH340 and Prolific chips
  are all recognized). Find its COM number in **Device Manager → Ports (COM & LPT)**. The program
  lists likely USB-RS485 adapters first.
* If nothing answers, **swap A and B**. Vendors don't agree on which line is "A".

## 3. Program a reader for OnGuard (GUI)

1. **COM port**: pick the adapter. Choose *DEMO - simulated reader* to practice without hardware.
2. **Find reader**: the program detects the reader's current address and baud rate and fills them in.
   A factory Veridt reader is at **address 1, 9600 baud**.
3. **1. Program address / baud** tab:
   * Pick a **preset** or enter the address and baud rate you'll use in OnGuard.
     The default preset is **address 0 @ 9600**, which matches LenelS2's fix for Veridt readers (see section 5).
   * Optionally type the **door/label** (for example *Main lobby entry, SN M808260057*) so the log
     ties the OSDP serial number to the physical reader.
   * Click **Program reader**. The program sends `osdp_COMSET`, switches to the new settings and confirms
     the same reader (same serial number) answers.
4. **Remove power** from the reader for about 5 seconds, restore it, then click **Verify after power cycle**.
   A PASS means the reader keeps its settings through a power loss. A FAIL tells you where the reader
   came back.
5. Copy the **Enter these settings in OnGuard** panel into the reader's properties in OnGuard
   System Administration (Access Control → Readers and Doors). Menu and field names vary between
   OnGuard versions.
6. Optional: on **3. Test**, start the **card monitor** and badge a card. The bit length shown is what
   the OnGuard card format must match. The LED and buzzer buttons confirm the reader responds to commands.
7. To do a batch, use **Find + program next reader…** for each one.

Every program, verify and key action is appended to `programming_log.csv` (button: *Log → Open
programming log folder*).

## 4. Secure channel (encryption)

OSDP Secure Channel protects the reader-to-panel link with AES-128. A new reader is in **install
mode**: it accepts the published default key (SCBK-D).

* **Usual OnGuard approach**: leave the reader in install mode. When you enable encryption for the
  reader in OnGuard, the panel installs its own key. Use **Test secure channel** with the default
  key to confirm the reader is still in install mode before you hang it.
* **Install a custom key** (*2. Secure channel → Generate random → Install key*) only if your OnGuard
  version lets you enter that same key for the reader. The program installs the key, then reconnects
  with it to prove it works.
* **Record every installed key.** Once a reader has a custom key, you need that key to change its
  settings again. The key is written to the programming log unless you untick the option; protect that file.
* If a command fails with **"Encrypted communication required"**, the reader already has a key, often
  one installed by a panel. Enter that key, or get a reset/configuration card from Veridt.

## 5. Veridt-specific notes

These are the reasons the *Verify after power cycle* step exists:

* **Factory defaults:** address 1, 9600 baud ([Veridt Tech Note 123][tn123]).
* **A baud change is only saved if the address changes too.** Veridt TN 123 says a baud change sent
  with `osdp_COMSET` "will not be saved if the reader ID is not changed", and a reader left at
  the default ID 1 "will always initialize at 9600 baud". The program warns you before it sends
  such a change, and the built-in simulator reproduces this behavior.
* **Readers going offline after a power event:** LenelS2 knowledge base article *"Veridt OSDP readers
  not coming online after power event"* says pre-2022 Veridt OSDP readers didn't hold their OSDP ID and baud
  rate. The fix was to set **ID 0 at 9600 baud** (Veridt used configuration cards for this) and to
  **set the baud rate explicitly in OnGuard**, because OnGuard 8.1 doesn't auto-negotiate it.
  This program does the same thing over RS-485 and then verifies it.
* RS-485 colors (Stealth Series Installation Guide, [Tech Note 121][tn121]): yellow = RS-485 + (A),
  blue = RS-485 − (B).

[tn123]: https://veridt.com/wp-content/uploads/2017/03/Veridt-Tech-Note-123-OSDP-Readers.pdf
[tn121]: https://veridt.com/wp-content/uploads/2024/12/Veridt-Tech-Note-121-Stealth-Installation-Guide-Rev-9-Dec-2024.pdf

### What this program doesn't do

* **Veridt-proprietary settings**, such as Wiegand output format, authentication modes or other
  MultiMode SDK features, aren't publicly documented, so they aren't implemented. The *Advanced* tab
  can send any raw OSDP command (including `osdp_MFG`) if Veridt support gives you one.
* Firmware updates and Veridt configuration cards.
* Recovering a reader whose custom secure channel key is unknown.

## 6. Troubleshooting

| Symptom | Check |
| --- | --- |
| No COM port listed | Unplug and replug the adapter; check Device Manager; install the adapter chip's driver |
| "No reader answered" | 12 VDC at the reader; swap A/B; ground connected; panel disconnected; only one reader on the line |
| Found, but program fails with NAK "Encrypted communication required" | The reader has a key installed; see section 4 |
| Verify after power cycle FAILs and shows baud 9600 | The address wasn't changed with the baud (see section 5); program a different address |
| Garbage or CRC errors in the Log tab | Long or unterminated cable, wrong ground, or a second device transmitting |
| Card monitor shows nothing | Confirm the card type is one the reader supports, and watch the Log tab for `osdp_RAW` replies. If the LED and buzzer tests work, the link is fine |

The **Log** tab shows every packet sent and received (hex) and can be saved for support.

## 7. Command line

`readerprog.exe` (or `python -m readerprog <command>` from source) does the same jobs without the GUI.
Add `--simulate` to any command to use the simulated reader, or `-v` to print the packet trace.

```
readerprog ports                                         list COM ports
readerprog scan    --port COM3                           find the reader's address and baud
readerprog info    --port COM3 --addr 1 --baud 9600      identity, capabilities, status
readerprog program --port COM3 --addr 1 --new-addr 0 --new-baud 9600
readerprog verify  --port COM3 --addr 0 --baud 9600      after a power cycle
readerprog test-sc --port COM3 --addr 0                  secure channel with the default key
readerprog set-key --port COM3 --addr 0 --new-key random
readerprog led green --port COM3 --addr 0
readerprog beep    --port COM3 --addr 0
readerprog watch   --port COM3 --addr 0                  print card reads (Ctrl+C to stop)
readerprog send 0x61 --data 00 --port COM3 --addr 0      raw command (advanced)
```

Add `--key <32 hex digits>` (or `--key default`) to run a command over secure channel.

## 8. Development

```
python -m pip install -r requirements-dev.txt
python -m pip install libosdp        # optional: enables the interoperability tests
python -m pytest
```

* `src/readerprog/osdp/` is a small OSDP control-panel stack: packet framing, CRC-16, secure
  channel (AES-128 session keys, cryptograms, MAC chaining, data encryption), reply decoding and
  discovery.
* `src/readerprog/sim.py` is the simulated Veridt-style reader used for demo mode and tests.
* `tests/test_libosdp_interop.py` runs this code against [libosdp](https://github.com/goToMain/libosdp),
  an independent OSDP implementation. It covers plain commands, secure channel with the default and custom keys,
  encrypted LED/buzzer commands, key installation and address changes. It also runs libosdp's control panel
  against the simulator.
* `tests/test_gui_smoke.py` drives the real GUI in demo mode (it needs a display; CI uses Xvfb on Linux).
* `.github/workflows/osdp-reader-programmer.yml` runs the tests on Linux and Windows, builds both
  .exe files with PyInstaller, smoke-tests the CLI exe and uploads them as an artifact.

Protocol reference: SIA OSDP v2.2 / IEC 60839-11-5.
