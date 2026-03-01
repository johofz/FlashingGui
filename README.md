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

1. Select your target MCU family from the dropdown
2. Browse for a firmware file (`.bin`, `.hex`, or `.elf`)
3. Click **Connect** to verify ST-Link connectivity
4. Click **Flash** to program the firmware
5. Use **Erase** for full chip erase or **Reset** to restart the MCU

## Supported Targets

STM32F0, STM32F1, STM32F2, STM32F3, STM32F4, STM32F7, STM32G0, STM32G4,
STM32H7, STM32L0, STM32L1, STM32L4, STM32L5, STM32U5, STM32WB, STM32WL, STM32C0
