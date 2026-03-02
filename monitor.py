"""OpenOCD variable monitor using server mode and TCL RPC interface.

Starts OpenOCD as a persistent server process and communicates via the
TCL RPC port (6666) to periodically read MCU memory.
"""

import re
import socket
import struct

from PyQt5.QtCore import QObject, QProcess, QTimer, pyqtSignal

from flasher import TARGETS, find_openocd

# TCL RPC protocol uses 0x1a as command delimiter
_TCL_DELIMITER = b"\x1a"
_TCL_PORT = 6666

# Data type definitions: (struct format char, byte size)
VARIABLE_TYPES = {
    "uint8_t": ("B", 1),
    "int8_t": ("b", 1),
    "uint16_t": ("H", 2),
    "int16_t": ("h", 2),
    "uint32_t": ("I", 4),
    "int32_t": ("i", 4),
    "float": ("f", 4),
}

# OpenOCD memory read command by byte size
_READ_CMD = {1: "mdb", 2: "mdh", 4: "mdw"}

# Parse OpenOCD memory read response, e.g. "0x20000000: 0012abcd"
_RE_MEM_VALUE = re.compile(r"0x[0-9a-fA-F]+:\s+([0-9a-fA-F]+)")


def interpret_value(raw_int, type_name):
    """Convert a raw integer to a typed Python value.

    Args:
        raw_int: Integer read from memory.
        type_name: One of VARIABLE_TYPES keys.

    Returns:
        Formatted string representation.
    """
    fmt_char, size = VARIABLE_TYPES.get(type_name, ("I", 4))
    # Pack as unsigned integer, then unpack as the target type (little-endian)
    try:
        raw_bytes = raw_int.to_bytes(size, byteorder="little")
        value = struct.unpack(f"<{fmt_char}", raw_bytes)[0]
        if type_name == "float":
            return f"{value:.6g}"
        return str(value)
    except (OverflowError, struct.error):
        return f"0x{raw_int:X}"


class OpenOCDMonitor(QObject):
    """Manages a persistent OpenOCD server for variable monitoring."""

    output_received = pyqtSignal(str)
    connected = pyqtSignal()
    disconnected = pyqtSignal()
    connection_error = pyqtSignal(str)
    values_updated = pyqtSignal(dict)  # {symbol_name: (raw_int, type_name)}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process = None
        self._socket = None
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_variables)
        self._connect_timer = QTimer(self)
        self._connect_timer.setSingleShot(True)
        self._connect_timer.timeout.connect(self._try_connect_socket)
        self._watched_symbols = []  # list of (name, address, size, type_name)
        self._poll_interval = 500
        self._is_connected = False

    def is_connected(self):
        return self._is_connected

    def is_server_running(self):
        return self._process is not None and self._process.state() != QProcess.NotRunning

    # ── Server lifecycle ─────────────────────────────────────────────

    def start_server(self, target_name):
        """Start OpenOCD in server mode for the given target."""
        if self.is_server_running():
            self.output_received.emit("Server is already running.\n")
            return

        target_cfg = TARGETS.get(target_name)
        if not target_cfg:
            self.connection_error.emit(f"Unknown target: {target_name}")
            return

        openocd_path = find_openocd()
        if not openocd_path:
            self.connection_error.emit(
                "OpenOCD not found. Please install OpenOCD and ensure it is in your PATH."
            )
            return

        args = [
            "-f", "interface/stlink.cfg",
            "-f", f"target/{target_cfg}",
            "-c", "init",
            "-c", "reset halt",
        ]

        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._on_server_output)
        self._process.finished.connect(self._on_server_finished)

        self.output_received.emit(f"$ openocd {' '.join(args)}\n")
        self._process.start(openocd_path, args)

        # Wait for OpenOCD to initialize before connecting socket
        self._connect_timer.start(2000)

    def stop_server(self):
        """Stop the OpenOCD server and clean up."""
        self._poll_timer.stop()
        self._connect_timer.stop()

        if self._socket:
            try:
                self._send_tcl_command("shutdown")
            except Exception:
                pass
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None

        if self._process and self._process.state() != QProcess.NotRunning:
            self._process.kill()
            self._process.waitForFinished(3000)
        self._process = None

        was_connected = self._is_connected
        self._is_connected = False
        if was_connected:
            self.output_received.emit("Monitor disconnected.\n")
            self.disconnected.emit()

    # ── Polling control ──────────────────────────────────────────────

    def start_polling(self, symbols):
        """Start periodic reading of the given symbols.

        Args:
            symbols: List of tuples (name, address, size, type_name).
        """
        self._watched_symbols = list(symbols)
        if not self._watched_symbols:
            self.output_received.emit("No variables selected for monitoring.\n")
            return
        count = len(self._watched_symbols)
        self.output_received.emit(
            f"Monitoring {count} variable(s) every {self._poll_interval}ms\n"
        )
        self._poll_timer.start(self._poll_interval)

    def stop_polling(self):
        """Stop periodic reading."""
        self._poll_timer.stop()
        self.output_received.emit("Polling stopped.\n")

    def set_poll_interval(self, ms):
        """Update the polling interval."""
        self._poll_interval = max(50, ms)
        if self._poll_timer.isActive():
            self._poll_timer.setInterval(self._poll_interval)

    def update_watched_symbols(self, symbols):
        """Update the list of watched symbols without restarting polling."""
        self._watched_symbols = list(symbols)

    # ── TCL RPC communication ────────────────────────────────────────

    def _try_connect_socket(self):
        """Attempt to connect to OpenOCD's TCL RPC port."""
        if not self.is_server_running():
            self.connection_error.emit("OpenOCD server is not running.")
            return

        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.settimeout(2.0)
            self._socket.connect(("127.0.0.1", _TCL_PORT))
            self._socket.settimeout(1.0)
            self._is_connected = True
            self.output_received.emit("Connected to OpenOCD TCL interface.\n")
            self.connected.emit()
        except (socket.error, OSError) as e:
            self._socket = None
            self.connection_error.emit(f"Could not connect to OpenOCD TCL port: {e}")

    def _send_tcl_command(self, cmd):
        """Send a command via TCL RPC and return the response string."""
        if not self._socket:
            return None
        try:
            self._socket.sendall(cmd.encode("utf-8") + _TCL_DELIMITER)
            response = b""
            while True:
                chunk = self._socket.recv(4096)
                if not chunk:
                    break
                response += chunk
                if _TCL_DELIMITER in response:
                    break
            # Strip delimiter and decode
            text = response.replace(_TCL_DELIMITER, b"").decode("utf-8", errors="replace").strip()
            return text
        except (socket.error, OSError):
            return None

    def _read_memory(self, address, size):
        """Read memory at the given address and return the raw integer value."""
        cmd_name = _READ_CMD.get(size, "mdw")
        cmd = f"{cmd_name} 0x{address:08X}"
        response = self._send_tcl_command(cmd)
        if response is None:
            return None
        m = _RE_MEM_VALUE.search(response)
        if m:
            return int(m.group(1), 16)
        return None

    # ── Internal slots ───────────────────────────────────────────────

    def _poll_variables(self):
        """Read all watched variables and emit values_updated."""
        if not self._is_connected or not self._socket:
            return

        results = {}
        for name, address, size, type_name in self._watched_symbols:
            raw = self._read_memory(address, size)
            if raw is not None:
                results[name] = (raw, type_name)
            else:
                results[name] = None

        if results:
            self.values_updated.emit(results)

    def _on_server_output(self):
        if not self._process:
            return
        data = self._process.readAllStandardOutput()
        text = bytes(data).decode("utf-8", errors="replace")
        self.output_received.emit(text)

    def _on_server_finished(self, exit_code, _exit_status):
        self._poll_timer.stop()
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None
        self._process = None
        self._is_connected = False
        self.output_received.emit(f"OpenOCD server exited (code {exit_code}).\n")
        self.disconnected.emit()
