import os
import re
import shutil

from PyQt5.QtCore import QObject, QProcess, pyqtSignal


TARGETS = {
    "STM32F0": "stm32f0x.cfg",
    "STM32F1": "stm32f1x.cfg",
    "STM32F2": "stm32f2x.cfg",
    "STM32F3": "stm32f3x.cfg",
    "STM32F4": "stm32f4x.cfg",
    "STM32F7": "stm32f7x.cfg",
    "STM32G0": "stm32g0x.cfg",
    "STM32G4": "stm32g4x.cfg",
    "STM32H7": "stm32h7x.cfg",
    "STM32L0": "stm32l0x.cfg",
    "STM32L1": "stm32l1x.cfg",
    "STM32L4": "stm32l4x.cfg",
    "STM32L5": "stm32l5x.cfg",
    "STM32U5": "stm32u5x.cfg",
    "STM32WB": "stm32wbx.cfg",
    "STM32WL": "stm32wlx.cfg",
    "STM32C0": "stm32c0x.cfg",
}


def find_openocd():
    """Return the path to the openocd binary, or None."""
    return shutil.which("openocd")


class OpenOCDFlasher(QObject):
    """Manages OpenOCD processes for flashing STM32 MCUs."""

    output_received = pyqtSignal(str)
    process_finished = pyqtSignal(int, str)  # exit_code, operation_name
    progress_updated = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process = None
        self._operation = ""
        self._output_buffer = ""

    def is_running(self):
        return self._process is not None and self._process.state() != QProcess.NotRunning

    def stop(self):
        if self._process and self._process.state() != QProcess.NotRunning:
            self._process.kill()
            self._process.waitForFinished(3000)

    def connect_target(self, target_name):
        """Probe the target MCU to verify connectivity."""
        target_cfg = TARGETS.get(target_name)
        if not target_cfg:
            self.output_received.emit(f"Error: Unknown target '{target_name}'\n")
            self.process_finished.emit(1, "connect")
            return
        commands = [
            "-f", "interface/stlink.cfg",
            "-f", f"target/{target_cfg}",
            "-c", "init",
            "-c", "reset halt",
            "-c", "flash info 0",
            "-c", "shutdown",
        ]
        self._run_openocd(commands, "connect")

    def flash(self, target_name, firmware_path):
        """Flash firmware to the target MCU."""
        target_cfg = TARGETS.get(target_name)
        if not target_cfg:
            self.output_received.emit(f"Error: Unknown target '{target_name}'\n")
            self.process_finished.emit(1, "flash")
            return

        if not os.path.isfile(firmware_path):
            self.output_received.emit(f"Error: File not found: {firmware_path}\n")
            self.process_finished.emit(1, "flash")
            return

        ext = os.path.splitext(firmware_path)[1].lower()

        if ext == ".bin":
            program_cmd = f"program {{{firmware_path}}} verify reset exit 0x08000000"
        else:
            # .elf and .hex contain address information
            program_cmd = f"program {{{firmware_path}}} verify reset exit"

        commands = [
            "-f", "interface/stlink.cfg",
            "-f", f"target/{target_cfg}",
            "-c", program_cmd,
        ]
        self._run_openocd(commands, "flash")

    def erase(self, target_name):
        """Full chip erase."""
        target_cfg = TARGETS.get(target_name)
        if not target_cfg:
            self.output_received.emit(f"Error: Unknown target '{target_name}'\n")
            self.process_finished.emit(1, "erase")
            return
        commands = [
            "-f", "interface/stlink.cfg",
            "-f", f"target/{target_cfg}",
            "-c", "init",
            "-c", "reset halt",
            "-c", "flash erase_sector 0 0 last",
            "-c", "shutdown",
        ]
        self._run_openocd(commands, "erase")

    def reset_target(self, target_name):
        """Reset the target MCU."""
        target_cfg = TARGETS.get(target_name)
        if not target_cfg:
            self.output_received.emit(f"Error: Unknown target '{target_name}'\n")
            self.process_finished.emit(1, "reset")
            return
        commands = [
            "-f", "interface/stlink.cfg",
            "-f", f"target/{target_cfg}",
            "-c", "init",
            "-c", "reset run",
            "-c", "shutdown",
        ]
        self._run_openocd(commands, "reset")

    # --- internal ---

    def _run_openocd(self, args, operation):
        if self.is_running():
            self.output_received.emit("Error: A process is already running.\n")
            return

        openocd_path = find_openocd()
        if not openocd_path:
            self.output_received.emit(
                "Error: OpenOCD not found. Please install OpenOCD and ensure it is in your PATH.\n"
            )
            self.process_finished.emit(1, operation)
            return

        self._operation = operation
        self._output_buffer = ""
        self.progress_updated.emit(0)

        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._on_stdout)
        self._process.finished.connect(self._on_finished)

        self.output_received.emit(f"$ openocd {' '.join(args)}\n")
        self._process.start(openocd_path, args)

    def _on_stdout(self):
        if not self._process:
            return
        data = self._process.readAllStandardOutput()
        text = bytes(data).decode("utf-8", errors="replace")
        self._output_buffer += text
        self.output_received.emit(text)
        self._parse_progress(text)

    def _on_finished(self, exit_code, _exit_status):
        op = self._operation
        if exit_code == 0:
            self.progress_updated.emit(100)
        self._process = None
        self.process_finished.emit(exit_code, op)

    def _parse_progress(self, text):
        """Try to extract progress from OpenOCD output."""
        # OpenOCD prints lines like "** Programming Started **" and "** Verified OK **"
        lower = text.lower()
        if "programming started" in lower:
            self.progress_updated.emit(10)
        elif re.search(r"wrote \d+ bytes", lower):
            self.progress_updated.emit(70)
        elif "verified ok" in lower:
            self.progress_updated.emit(90)
        elif "programming finished" in lower or "** programming finished **" in lower:
            self.progress_updated.emit(95)
        elif "erased" in lower:
            self.progress_updated.emit(80)
        elif "halted" in lower and self._operation == "connect":
            self.progress_updated.emit(50)
        elif "flash info" in lower:
            self.progress_updated.emit(80)
