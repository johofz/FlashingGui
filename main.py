#!/usr/bin/env python3
"""STM32 Flash Tool - A modern GUI for flashing and verifying STM32 MCUs via OpenOCD."""

import os
import sys

from PyQt5.QtCore import Qt, QSettings
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from flasher import TARGETS, OpenOCDFlasher, find_openocd
from map_parser import MapSymbol, default_type_for_size, parse_map_file
from monitor import VARIABLE_TYPES, OpenOCDMonitor, interpret_value


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("STM32 Flash Tool")
        self.setMinimumSize(760, 700)
        self.resize(800, 820)

        self.settings = QSettings("STM32FlashTool", "STM32FlashTool")
        self.flasher = OpenOCDFlasher(self)
        self.monitor = OpenOCDMonitor(self)
        self._symbols = []  # unified symbol list (MapSymbol or ElfSymbol)

        self._build_ui()
        self._connect_signals()
        self._restore_settings()
        self._check_openocd()
        self._update_flash_button_states()
        self._update_monitor_button_states()

    # ── UI Construction ──────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 12, 16, 8)
        root.setSpacing(10)

        # ── 1. Configuration ─────────────────────────────────────────
        config_group = QGroupBox("Configuration")
        config_layout = QVBoxLayout(config_group)
        config_layout.setSpacing(8)

        # Target MCU
        mcu_row = QHBoxLayout()
        mcu_label = QLabel("Target MCU:")
        mcu_label.setFixedWidth(100)
        self.target_combo = QComboBox()
        self.target_combo.addItems(sorted(TARGETS.keys()))
        self.target_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        mcu_row.addWidget(mcu_label)
        mcu_row.addWidget(self.target_combo)
        config_layout.addLayout(mcu_row)

        # Firmware file
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
        config_layout.addLayout(fw_row)

        # Map file
        map_row = QHBoxLayout()
        map_label = QLabel("Map File:")
        map_label.setFixedWidth(100)
        self.map_edit = QLineEdit()
        self.map_edit.setPlaceholderText("Select a linker .map file (optional)")
        self.map_browse_btn = QPushButton("Browse")
        self.map_browse_btn.setObjectName("browseBtn")
        self.map_load_btn = QPushButton("Load")
        self.map_load_btn.setObjectName("mapLoadBtn")
        map_row.addWidget(map_label)
        map_row.addWidget(self.map_edit)
        map_row.addWidget(self.map_browse_btn)
        map_row.addWidget(self.map_load_btn)
        config_layout.addLayout(map_row)

        # ELF file (for struct info)
        elf_row = QHBoxLayout()
        elf_label = QLabel("ELF File:")
        elf_label.setFixedWidth(100)
        self.elf_edit = QLineEdit()
        self.elf_edit.setPlaceholderText("Select an ELF file for struct info (optional)")
        self.elf_browse_btn = QPushButton("Browse")
        self.elf_browse_btn.setObjectName("browseBtn")
        self.elf_load_btn = QPushButton("Load")
        self.elf_load_btn.setObjectName("mapLoadBtn")
        elf_row.addWidget(elf_label)
        elf_row.addWidget(self.elf_edit)
        elf_row.addWidget(self.elf_browse_btn)
        elf_row.addWidget(self.elf_load_btn)
        config_layout.addLayout(elf_row)

        root.addWidget(config_group)

        # ── 2. Flash ─────────────────────────────────────────────────
        flash_group = QGroupBox("Flash")
        flash_layout = QVBoxLayout(flash_group)
        flash_layout.setSpacing(8)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
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

        btn_row.addWidget(self.connect_btn)
        btn_row.addWidget(self.flash_btn)
        btn_row.addWidget(self.erase_btn)
        btn_row.addWidget(self.reset_btn)
        btn_row.addStretch()
        btn_row.addWidget(self.abort_btn)
        flash_layout.addLayout(btn_row)

        # Progress bar + status
        progress_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%p%")
        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setFixedWidth(200)
        progress_row.addWidget(self.progress_bar)
        progress_row.addWidget(self.status_label)
        flash_layout.addLayout(progress_row)

        root.addWidget(flash_group)

        # ── 3. Splitter: Variable Monitor + Console ──────────────────
        splitter = QSplitter(Qt.Vertical)

        # -- Variable Monitor --
        monitor_group = QGroupBox("Variable Monitor")
        monitor_layout = QVBoxLayout(monitor_group)
        monitor_layout.setSpacing(6)

        # Monitor control row
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(8)

        # Filter
        filter_label = QLabel("Filter:")
        self.var_filter_edit = QLineEdit()
        self.var_filter_edit.setPlaceholderText("Filter variables...")
        self.var_filter_edit.setMaximumWidth(200)

        interval_label = QLabel("Interval:")
        self.poll_spin = QSpinBox()
        self.poll_spin.setRange(50, 10000)
        self.poll_spin.setSingleStep(100)
        self.poll_spin.setValue(500)
        self.poll_spin.setSuffix(" ms")

        self.monitor_start_btn = QPushButton("Start Monitoring")
        self.monitor_start_btn.setObjectName("monitorStartBtn")
        self.monitor_start_btn.setToolTip("Start OpenOCD server and begin reading variables")
        self.monitor_stop_btn = QPushButton("Stop")
        self.monitor_stop_btn.setObjectName("monitorStopBtn")
        self.monitor_stop_btn.setToolTip("Stop reading and shut down OpenOCD server")
        self.monitor_stop_btn.setEnabled(False)

        ctrl_row.addWidget(filter_label)
        ctrl_row.addWidget(self.var_filter_edit)
        ctrl_row.addWidget(interval_label)
        ctrl_row.addWidget(self.poll_spin)
        ctrl_row.addStretch()
        ctrl_row.addWidget(self.monitor_start_btn)
        ctrl_row.addWidget(self.monitor_stop_btn)
        monitor_layout.addLayout(ctrl_row)

        # Variable tree (replaces table for hierarchical struct display)
        self.var_tree = QTreeWidget()
        self.var_tree.setColumnCount(6)
        self.var_tree.setHeaderLabels(["Watch", "Name", "Address", "Size", "Type", "Value"])
        self.var_tree.setAlternatingRowColors(True)
        self.var_tree.setSelectionMode(QTreeWidget.NoSelection)
        self.var_tree.setRootIsDecorated(True)

        header = self.var_tree.header()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.resizeSection(0, 50)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        header.resizeSection(3, 50)
        header.setSectionResizeMode(4, QHeaderView.Fixed)
        header.resizeSection(4, 100)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)

        monitor_layout.addWidget(self.var_tree, stretch=1)
        splitter.addWidget(monitor_group)

        # -- Console Output --
        console_group = QGroupBox("Console Output")
        console_layout = QVBoxLayout(console_group)
        console_layout.setSpacing(4)

        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(5000)
        console_layout.addWidget(self.console)

        clear_row = QHBoxLayout()
        clear_row.addStretch()
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setObjectName("clearBtn")
        clear_row.addWidget(self.clear_btn)
        console_layout.addLayout(clear_row)

        splitter.addWidget(console_group)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        root.addWidget(splitter, stretch=1)

        # ── Status Bar ───────────────────────────────────────────────
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

    # ── Signals ──────────────────────────────────────────────────────

    def _connect_signals(self):
        # Configuration
        self.browse_btn.clicked.connect(self._browse_firmware)
        self.map_browse_btn.clicked.connect(self._browse_map_file)
        self.map_load_btn.clicked.connect(self._load_map_file)
        self.elf_browse_btn.clicked.connect(self._browse_elf_file)
        self.elf_load_btn.clicked.connect(self._load_elf_file)
        self.firmware_edit.textChanged.connect(self._update_flash_button_states)
        self.var_filter_edit.textChanged.connect(self._filter_variables)

        # Flash
        self.connect_btn.clicked.connect(self._do_connect)
        self.flash_btn.clicked.connect(self._do_flash)
        self.erase_btn.clicked.connect(self._do_erase)
        self.reset_btn.clicked.connect(self._do_reset)
        self.abort_btn.clicked.connect(self._do_abort)
        self.clear_btn.clicked.connect(self.console.clear)

        self.flasher.output_received.connect(self._append_output)
        self.flasher.process_finished.connect(self._on_process_finished)
        self.flasher.progress_updated.connect(self.progress_bar.setValue)

        # Monitor
        self.monitor_start_btn.clicked.connect(self._start_monitoring)
        self.monitor_stop_btn.clicked.connect(self._stop_monitoring)
        self.poll_spin.valueChanged.connect(self.monitor.set_poll_interval)

        self.monitor.output_received.connect(self._append_output)
        self.monitor.connected.connect(self._on_monitor_connected)
        self.monitor.disconnected.connect(self._on_monitor_disconnected)
        self.monitor.connection_error.connect(self._on_monitor_error)
        self.monitor.values_updated.connect(self._update_variable_values)

    # ── Settings ─────────────────────────────────────────────────────

    def _restore_settings(self):
        fw = self.settings.value("firmware_path", "")
        if fw:
            self.firmware_edit.setText(fw)
        map_path = self.settings.value("map_file_path", "")
        if map_path:
            self.map_edit.setText(map_path)
        elf_path = self.settings.value("elf_file_path", "")
        if elf_path:
            self.elf_edit.setText(elf_path)
        target = self.settings.value("target", "")
        if target:
            idx = self.target_combo.findText(target)
            if idx >= 0:
                self.target_combo.setCurrentIndex(idx)
        interval = self.settings.value("poll_interval", 500, type=int)
        self.poll_spin.setValue(interval)
        geo = self.settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)

    def _save_settings(self):
        self.settings.setValue("firmware_path", self.firmware_edit.text())
        self.settings.setValue("map_file_path", self.map_edit.text())
        self.settings.setValue("elf_file_path", self.elf_edit.text())
        self.settings.setValue("target", self.target_combo.currentText())
        self.settings.setValue("poll_interval", self.poll_spin.value())
        self.settings.setValue("geometry", self.saveGeometry())

    def closeEvent(self, event):
        self._save_settings()
        if self.monitor.is_connected():
            self.monitor.stop_server()
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

    # ══════════════════════════════════════════════════════════════════
    # FLASH
    # ══════════════════════════════════════════════════════════════════

    def _browse_firmware(self):
        start_dir = os.path.dirname(self.firmware_edit.text()) or os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Firmware File", start_dir,
            "Firmware Files (*.bin *.hex *.elf);;All Files (*)",
        )
        if path:
            self.firmware_edit.setText(path)

    def _do_connect(self):
        self._stop_monitor_if_running()
        self._start_flash_operation("connect")
        self.flasher.connect_target(self.target_combo.currentText())

    def _do_flash(self):
        fw = self.firmware_edit.text().strip()
        if not fw:
            self._append_output("Error: No firmware file selected.\n")
            return
        if not os.path.isfile(fw):
            self._append_output(f"Error: File not found: {fw}\n")
            return
        self._stop_monitor_if_running()
        self._start_flash_operation("flash")
        self.flasher.flash(self.target_combo.currentText(), fw)

    def _do_erase(self):
        self._stop_monitor_if_running()
        self._start_flash_operation("erase")
        self.flasher.erase(self.target_combo.currentText())

    def _do_reset(self):
        self._stop_monitor_if_running()
        self._start_flash_operation("reset")
        self.flasher.reset_target(self.target_combo.currentText())

    def _do_abort(self):
        self.flasher.stop()
        self._append_output("\n--- Operation aborted by user ---\n")
        self._set_flash_busy(False)
        self.status_label.setText("Aborted")

    def _start_flash_operation(self, name):
        labels = {
            "connect": "Connecting...",
            "flash": "Flashing...",
            "erase": "Erasing...",
            "reset": "Resetting...",
        }
        self.status_label.setText(labels.get(name, "Working..."))
        self.progress_bar.setValue(0)
        self._set_flash_busy(True)

    def _set_flash_busy(self, busy):
        self.connect_btn.setEnabled(not busy)
        self.flash_btn.setEnabled(not busy and bool(self.firmware_edit.text().strip()))
        self.erase_btn.setEnabled(not busy)
        self.reset_btn.setEnabled(not busy)
        self.target_combo.setEnabled(not busy)
        self.firmware_edit.setEnabled(not busy)
        self.browse_btn.setEnabled(not busy)
        self.abort_btn.setVisible(busy)

    def _update_flash_button_states(self):
        has_firmware = bool(self.firmware_edit.text().strip())
        if not self.flasher.is_running():
            self.flash_btn.setEnabled(has_firmware)

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
        self._set_flash_busy(False)

    def _stop_monitor_if_running(self):
        if self.monitor.is_connected():
            self._append_output("Stopping variable monitor before flashing...\n")
            self.monitor.stop_server()

    # ══════════════════════════════════════════════════════════════════
    # VARIABLE MONITOR
    # ══════════════════════════════════════════════════════════════════

    def _browse_map_file(self):
        start_dir = os.path.dirname(self.map_edit.text()) or os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Map File", start_dir,
            "Map Files (*.map);;All Files (*)",
        )
        if path:
            self.map_edit.setText(path)

    def _browse_elf_file(self):
        start_dir = os.path.dirname(self.elf_edit.text()) or os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(
            self, "Select ELF File", start_dir,
            "ELF Files (*.elf);;All Files (*)",
        )
        if path:
            self.elf_edit.setText(path)

    def _load_map_file(self):
        path = self.map_edit.text().strip()
        if not path:
            self._append_output("Error: No map file selected.\n")
            return
        if not os.path.isfile(path):
            self._append_output(f"Error: File not found: {path}\n")
            return
        try:
            self._symbols = parse_map_file(path)
        except Exception as e:
            self._append_output(f"Error parsing map file: {e}\n")
            self._symbols = []
            return
        count = len(self._symbols)
        self._append_output(f"Loaded {count} variable(s) from {os.path.basename(path)}\n")
        self._populate_variable_tree()
        self._update_monitor_button_states()

    def _load_elf_file(self):
        path = self.elf_edit.text().strip()
        if not path:
            self._append_output("Error: No ELF file selected.\n")
            return
        if not os.path.isfile(path):
            self._append_output(f"Error: File not found: {path}\n")
            return
        try:
            from elf_parser import parse_elf_file
            elf_symbols = parse_elf_file(path)
        except ImportError:
            self._append_output(
                "Error: pyelftools not installed. Run: pip install pyelftools\n"
            )
            return
        except Exception as e:
            self._append_output(f"Error parsing ELF file: {e}\n")
            return

        # Convert ElfSymbol to MapSymbol for unified handling
        self._symbols = []
        for sym in elf_symbols:
            self._symbols.append(MapSymbol(
                name=sym.name,
                address=sym.address,
                size=sym.size,
                section=sym.section,
                type_name=sym.type_name,
                is_struct=sym.is_struct,
                members=sym.members,
            ))

        count = len(self._symbols)
        struct_count = sum(1 for s in self._symbols if s.is_struct)
        self._append_output(
            f"Loaded {count} variable(s) from {os.path.basename(path)} "
            f"({struct_count} struct(s))\n"
        )
        self._populate_variable_tree()
        self._update_monitor_button_states()

    def _populate_variable_tree(self):
        self.var_tree.clear()
        filter_text = self.var_filter_edit.text().strip().lower()

        for sym in self._symbols:
            if filter_text and filter_text not in sym.name.lower():
                continue

            item = self._create_tree_item(
                sym.name, sym.address, sym.size,
                sym.type_name or default_type_for_size(sym.size),
                sym.is_struct,
            )
            self.var_tree.addTopLevelItem(item)

            # Add widgets after item is in the tree
            self._setup_item_widgets(item, sym.is_struct,
                                     sym.type_name or default_type_for_size(sym.size))

            # Recursively add struct members
            if sym.is_struct and sym.members:
                self._add_struct_children(item, sym.address, sym.members)

    def _create_tree_item(self, name, address, size, type_name, is_struct):
        item = QTreeWidgetItem()
        item.setText(1, name)
        item.setText(2, f"0x{address:08X}")
        item.setText(3, str(size))
        item.setText(5, "---")
        # Store address and size for monitoring
        item.setData(1, Qt.UserRole, address)
        item.setData(1, Qt.UserRole + 1, size)
        item.setData(1, Qt.UserRole + 2, is_struct)
        # Center-align address, size, value columns
        item.setTextAlignment(2, Qt.AlignCenter)
        item.setTextAlignment(3, Qt.AlignCenter)
        item.setTextAlignment(5, Qt.AlignCenter)
        return item

    def _setup_item_widgets(self, item, is_struct, type_name):
        """Set up checkbox and type combo widgets for a tree item."""
        # Watch checkbox
        watch_widget = QWidget()
        watch_layout = QHBoxLayout(watch_widget)
        watch_layout.setContentsMargins(0, 0, 0, 0)
        watch_layout.setAlignment(Qt.AlignCenter)
        cb = QCheckBox()
        if is_struct:
            cb.setChecked(False)
            cb.setEnabled(False)  # can't watch a whole struct
        else:
            cb.setChecked(True)
        watch_layout.addWidget(cb)
        self.var_tree.setItemWidget(item, 0, watch_widget)

        # Type column
        if is_struct:
            item.setText(4, type_name)
        else:
            type_combo = QComboBox()
            type_combo.addItems(list(VARIABLE_TYPES.keys()))
            idx = type_combo.findText(type_name)
            if idx >= 0:
                type_combo.setCurrentIndex(idx)
            self.var_tree.setItemWidget(item, 4, type_combo)

    def _add_struct_children(self, parent_item, base_address, members):
        for member in members:
            abs_addr = base_address + member.offset
            item = self._create_tree_item(
                member.name, abs_addr, member.size,
                member.type_name, member.is_struct,
            )
            parent_item.addChild(item)

            # Add widgets after item is in the tree
            self._setup_item_widgets(item, member.is_struct, member.type_name)

            # Recurse for nested structs
            if member.is_struct and member.children:
                self._add_struct_children(item, abs_addr, member.children)

    def _filter_variables(self):
        self._populate_variable_tree()

    def _get_dotted_name(self, item):
        """Build the full dotted path for a tree item (e.g. 'ladeluxData.comData.txData')."""
        parts = []
        current = item
        while current is not None:
            parts.append(current.text(1))
            current = current.parent()
        parts.reverse()
        return ".".join(parts)

    def _get_watched_symbols(self):
        """Collect all checked leaf items from the tree for monitoring."""
        symbols = []
        self._collect_watched_recursive(self.var_tree.invisibleRootItem(), symbols)
        return symbols

    def _collect_watched_recursive(self, parent, symbols):
        for i in range(parent.childCount()):
            item = parent.child(i)
            is_struct = item.data(1, Qt.UserRole + 2)

            if not is_struct:
                # Leaf node: check if watched
                watch_widget = self.var_tree.itemWidget(item, 0)
                if watch_widget:
                    cb = watch_widget.findChild(QCheckBox)
                    if cb and cb.isChecked():
                        address = item.data(1, Qt.UserRole)
                        size = item.data(1, Qt.UserRole + 1)
                        type_combo = self.var_tree.itemWidget(item, 4)
                        type_name = type_combo.currentText() if type_combo else "uint32_t"
                        dotted_name = self._get_dotted_name(item)
                        symbols.append((dotted_name, address, size, type_name))

            # Recurse into children
            if item.childCount() > 0:
                self._collect_watched_recursive(item, symbols)

    def _start_monitoring(self):
        watched = self._get_watched_symbols()
        if not watched:
            self._append_output("No variables selected for monitoring.\n")
            return
        self.monitor.set_poll_interval(self.poll_spin.value())
        self._append_output(
            f"Starting OpenOCD server for {self.target_combo.currentText()}...\n"
        )
        self._pending_watch_symbols = watched
        self.monitor.start_server(self.target_combo.currentText())
        self._update_monitor_button_states()

    def _stop_monitoring(self):
        self.monitor.stop_polling()
        self.monitor.stop_server()
        self._update_monitor_button_states()

    def _on_monitor_connected(self):
        self._append_output("OpenOCD server ready. Starting variable polling...\n")
        if hasattr(self, "_pending_watch_symbols"):
            self.monitor.start_polling(self._pending_watch_symbols)
            del self._pending_watch_symbols
        self._update_monitor_button_states()

    def _on_monitor_disconnected(self):
        self._append_output("Monitor disconnected.\n")
        self._update_monitor_button_states()

    def _on_monitor_error(self, msg):
        self._append_output(f"Monitor error: {msg}\n")
        self._update_monitor_button_states()

    def _update_variable_values(self, values):
        """Update displayed values in the tree from monitor results."""
        self._update_values_recursive(self.var_tree.invisibleRootItem(), values)

    def _update_values_recursive(self, parent, values):
        for i in range(parent.childCount()):
            item = parent.child(i)
            is_struct = item.data(1, Qt.UserRole + 2)

            if not is_struct:
                dotted_name = self._get_dotted_name(item)
                if dotted_name in values:
                    result = values[dotted_name]
                    if result is None:
                        item.setText(5, "ERR")
                    else:
                        raw_int, type_name = result
                        item.setText(5, interpret_value(raw_int, type_name))

            # Recurse
            if item.childCount() > 0:
                self._update_values_recursive(item, values)

    def _update_monitor_button_states(self):
        has_symbols = len(self._symbols) > 0
        is_running = self.monitor.is_server_running()
        self.monitor_start_btn.setEnabled(has_symbols and not is_running)
        self.monitor_stop_btn.setEnabled(is_running)
        self.map_load_btn.setEnabled(not is_running)
        self.map_browse_btn.setEnabled(not is_running)
        self.map_edit.setEnabled(not is_running)
        self.elf_load_btn.setEnabled(not is_running)
        self.elf_browse_btn.setEnabled(not is_running)
        self.elf_edit.setEnabled(not is_running)

    # ── Shared helpers ───────────────────────────────────────────────

    def _append_output(self, text):
        self.console.moveCursor(self.console.textCursor().End)
        self.console.insertPlainText(text)
        self.console.ensureCursorVisible()


def load_stylesheet():
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
