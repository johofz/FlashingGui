#!/usr/bin/env python3
"""STM32 Flash Tool - A modern GUI for flashing STM32 MCUs via OpenOCD and ST-Link."""

import os
import sys

from PyQt5.QtCore import Qt, QSettings
from PyQt5.QtGui import QFont, QIcon
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from flasher import TARGETS, OpenOCDFlasher, find_openocd


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("STM32 Flash Tool")
        self.setMinimumSize(680, 560)
        self.resize(720, 620)

        self.settings = QSettings("STM32FlashTool", "STM32FlashTool")
        self.flasher = OpenOCDFlasher(self)

        self._build_ui()
        self._connect_signals()
        self._restore_settings()
        self._check_openocd()
        self._update_button_states()

    # ── UI Construction ──────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(16, 16, 16, 8)
        root_layout.setSpacing(12)

        # --- Target Configuration ---
        target_group = QGroupBox("Target Configuration")
        target_layout = QVBoxLayout(target_group)
        target_layout.setSpacing(10)

        # MCU row
        mcu_row = QHBoxLayout()
        mcu_label = QLabel("Target MCU:")
        mcu_label.setFixedWidth(100)
        self.target_combo = QComboBox()
        self.target_combo.addItems(sorted(TARGETS.keys()))
        self.target_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        mcu_row.addWidget(mcu_label)
        mcu_row.addWidget(self.target_combo)
        target_layout.addLayout(mcu_row)

        # Firmware row
        fw_row = QHBoxLayout()
        fw_label = QLabel("Firmware:")
        fw_label.setFixedWidth(100)
        self.firmware_edit = QLineEdit()
        self.firmware_edit.setPlaceholderText("Select a firmware file (.bin, .hex, .elf)")
        self.browse_btn = QPushButton("Browse")
        self.browse_btn.setObjectName("browseBtn")
        fw_row.addWidget(fw_label)
        fw_row.addWidget(self.firmware_edit)
        fw_row.addWidget(self.browse_btn)
        target_layout.addLayout(fw_row)

        root_layout.addWidget(target_group)

        # --- Actions ---
        actions_group = QGroupBox("Actions")
        actions_layout = QHBoxLayout(actions_group)
        actions_layout.setSpacing(10)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setToolTip("Probe the target MCU via ST-Link")

        self.flash_btn = QPushButton("Flash")
        self.flash_btn.setObjectName("flashBtn")
        self.flash_btn.setToolTip("Program firmware to the target MCU")

        self.erase_btn = QPushButton("Erase")
        self.erase_btn.setObjectName("eraseBtn")
        self.erase_btn.setToolTip("Full chip erase")

        self.reset_btn = QPushButton("Reset")
        self.reset_btn.setToolTip("Reset the target MCU")

        self.abort_btn = QPushButton("Abort")
        self.abort_btn.setObjectName("abortBtn")
        self.abort_btn.setToolTip("Stop the running operation")
        self.abort_btn.setVisible(False)

        actions_layout.addWidget(self.connect_btn)
        actions_layout.addWidget(self.flash_btn)
        actions_layout.addWidget(self.erase_btn)
        actions_layout.addWidget(self.reset_btn)
        actions_layout.addStretch()
        actions_layout.addWidget(self.abort_btn)

        root_layout.addWidget(actions_group)

        # --- Progress ---
        progress_group = QGroupBox("Progress")
        progress_layout = QVBoxLayout(progress_group)
        progress_layout.setSpacing(6)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%p%")
        progress_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("statusLabel")
        progress_layout.addWidget(self.status_label)

        root_layout.addWidget(progress_group)

        # --- Console ---
        console_group = QGroupBox("Console Output")
        console_layout = QVBoxLayout(console_group)
        console_layout.setSpacing(6)

        self.console = QPlainTextEdit()
        self.console.setObjectName("console")
        self.console.setReadOnly(True)
        self.console.setFont(QFont("Consolas", 11))
        self.console.setMaximumBlockCount(5000)
        console_layout.addWidget(self.console)

        clear_row = QHBoxLayout()
        clear_row.addStretch()
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setObjectName("clearBtn")
        clear_row.addWidget(self.clear_btn)
        console_layout.addLayout(clear_row)

        root_layout.addWidget(console_group, stretch=1)

        # --- Status Bar ---
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

    # ── Signals ──────────────────────────────────────────────────────

    def _connect_signals(self):
        self.browse_btn.clicked.connect(self._browse_firmware)
        self.connect_btn.clicked.connect(self._do_connect)
        self.flash_btn.clicked.connect(self._do_flash)
        self.erase_btn.clicked.connect(self._do_erase)
        self.reset_btn.clicked.connect(self._do_reset)
        self.abort_btn.clicked.connect(self._do_abort)
        self.clear_btn.clicked.connect(self.console.clear)
        self.firmware_edit.textChanged.connect(self._update_button_states)

        self.flasher.output_received.connect(self._append_output)
        self.flasher.process_finished.connect(self._on_process_finished)
        self.flasher.progress_updated.connect(self.progress_bar.setValue)

    # ── Settings ─────────────────────────────────────────────────────

    def _restore_settings(self):
        fw = self.settings.value("firmware_path", "")
        if fw:
            self.firmware_edit.setText(fw)
        target = self.settings.value("target", "")
        if target:
            idx = self.target_combo.findText(target)
            if idx >= 0:
                self.target_combo.setCurrentIndex(idx)
        geo = self.settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)

    def _save_settings(self):
        self.settings.setValue("firmware_path", self.firmware_edit.text())
        self.settings.setValue("target", self.target_combo.currentText())
        self.settings.setValue("geometry", self.saveGeometry())

    def closeEvent(self, event):
        self._save_settings()
        if self.flasher.is_running():
            self.flasher.stop()
        event.accept()

    # ── OpenOCD check ────────────────────────────────────────────────

    def _check_openocd(self):
        path = find_openocd()
        if path:
            self.status_bar.showMessage(f"OpenOCD: {path}")
        else:
            self.status_bar.showMessage("OpenOCD: NOT FOUND - Please install OpenOCD")
            self._append_output(
                "WARNING: OpenOCD was not found in your PATH.\n"
                "Install it via your package manager:\n"
                "  Ubuntu/Debian: sudo apt install openocd\n"
                "  macOS:         brew install openocd\n"
                "  Windows:       Download from https://openocd.org\n\n"
            )

    # ── Actions ──────────────────────────────────────────────────────

    def _browse_firmware(self):
        start_dir = os.path.dirname(self.firmware_edit.text()) or os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Firmware File",
            start_dir,
            "Firmware Files (*.bin *.hex *.elf);;All Files (*)",
        )
        if path:
            self.firmware_edit.setText(path)

    def _do_connect(self):
        self._start_operation("connect")
        self.flasher.connect_target(self.target_combo.currentText())

    def _do_flash(self):
        fw = self.firmware_edit.text().strip()
        if not fw:
            self._append_output("Error: No firmware file selected.\n")
            return
        if not os.path.isfile(fw):
            self._append_output(f"Error: File not found: {fw}\n")
            return
        self._start_operation("flash")
        self.flasher.flash(self.target_combo.currentText(), fw)

    def _do_erase(self):
        self._start_operation("erase")
        self.flasher.erase(self.target_combo.currentText())

    def _do_reset(self):
        self._start_operation("reset")
        self.flasher.reset_target(self.target_combo.currentText())

    def _do_abort(self):
        self.flasher.stop()
        self._append_output("\n--- Operation aborted by user ---\n")
        self._set_busy(False)
        self.status_label.setText("Aborted")

    # ── Helpers ──────────────────────────────────────────────────────

    def _start_operation(self, name):
        labels = {
            "connect": "Connecting...",
            "flash": "Flashing...",
            "erase": "Erasing...",
            "reset": "Resetting...",
        }
        self.status_label.setText(labels.get(name, "Working..."))
        self.progress_bar.setValue(0)
        self._set_busy(True)

    def _set_busy(self, busy):
        self.connect_btn.setEnabled(not busy)
        self.flash_btn.setEnabled(not busy and bool(self.firmware_edit.text().strip()))
        self.erase_btn.setEnabled(not busy)
        self.reset_btn.setEnabled(not busy)
        self.target_combo.setEnabled(not busy)
        self.firmware_edit.setEnabled(not busy)
        self.browse_btn.setEnabled(not busy)
        self.abort_btn.setVisible(busy)

    def _update_button_states(self):
        has_firmware = bool(self.firmware_edit.text().strip())
        if not self.flasher.is_running():
            self.flash_btn.setEnabled(has_firmware)

    def _append_output(self, text):
        self.console.moveCursor(self.console.textCursor().End)
        self.console.insertPlainText(text)
        self.console.ensureCursorVisible()

    def _on_process_finished(self, exit_code, operation):
        if exit_code == 0:
            labels = {
                "connect": "Connected successfully",
                "flash": "Flashing completed successfully",
                "erase": "Erase completed successfully",
                "reset": "Reset completed successfully",
            }
            self.status_label.setText(labels.get(operation, "Done"))
            self._append_output(f"\n--- {operation.capitalize()} finished (OK) ---\n\n")
        else:
            self.status_label.setText(f"{operation.capitalize()} failed (exit code {exit_code})")
            self._append_output(
                f"\n--- {operation.capitalize()} failed (exit code {exit_code}) ---\n\n"
            )
        self._set_busy(False)


def load_stylesheet():
    """Load the QSS stylesheet from the style.qss file."""
    qss_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "style.qss")
    if os.path.isfile(qss_path):
        with open(qss_path, "r") as f:
            return f.read()
    return ""


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("STM32 Flash Tool")
    app.setOrganizationName("STM32FlashTool")

    stylesheet = load_stylesheet()
    if stylesheet:
        app.setStyleSheet(stylesheet)

    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
