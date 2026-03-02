# STM32 Flash Tool

A modern GUI application for flashing STM32 microcontrollers via OpenOCD and ST-Link.

## Prerequisites

- Python 3.8+
- OpenOCD (`sudo apt install openocd` / `brew install openocd`)
- ST-Link debugger (V2 or V3)

## Installation

```bash
pip install -r requirements.txt
```

## Usage

```bash
python main.py
```

The tool is designed for flashing many MCUs sequentially and verifying
correct operation. The single-page workflow layout follows these steps:

### 1. Configure & Connect

- Select your target MCU family from the dropdown
- Browse for a firmware file (`.bin`, `.hex`, or `.elf`)
- Optionally load a GCC ARM linker `.map` file for variable monitoring
- Click **Connect** to verify ST-Link connectivity

### 2. Flash

- Click **Flash** to program the firmware
- Use **Erase** for full chip erase or **Reset** to restart the MCU
- Progress and status are shown in real-time

### 3. Monitor Variables

- After loading a `.map` file, select which variables to watch using the checkboxes
- Choose the correct data type for each variable (uint8_t, int16_t, float, etc.)
- Set the poll interval (default: 500ms)
- Click **Start Monitoring** to connect and begin live reading
- Variable values update in real-time in the table

The monitor uses OpenOCD's TCL RPC interface (port 6666) for efficient
periodic memory reads without reconnecting.

## Supported Targets

STM32F0, STM32F1, STM32F2, STM32F3, STM32F4, STM32F7, STM32G0, STM32G4,
STM32H7, STM32L0, STM32L1, STM32L4, STM32L5, STM32U5, STM32WB, STM32WL, STM32C0
