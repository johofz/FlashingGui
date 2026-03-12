"""Parser for GCC ARM linker .map files.

Extracts variable symbols (name, address, size) from .data and .bss sections.
"""

import re
from dataclasses import dataclass, field
from typing import List


@dataclass
class MapSymbol:
    name: str
    address: int
    size: int
    section: str  # ".bss" or ".data"
    type_name: str = ""
    is_struct: bool = False
    members: List = field(default_factory=list)  # List[StructMember] from elf_parser


# Matches an output section header, e.g.:
#   .bss            0x20000000        0x1c
RE_OUTPUT_SECTION = re.compile(
    r"^(\.(data|bss))\s+0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)"
)

# Matches an input section with size (single line), e.g.:
#   .bss.counter   0x20000008        0x4 main.o
RE_INPUT_SECTION = re.compile(
    r"^\s+(\.\S+)\s+0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)\s+\S+"
)

# Matches an input section name on its own line (continuation), e.g.:
#   .bss.statusByte
RE_INPUT_SECTION_NAME = re.compile(
    r"^\s+(\.[a-zA-Z_]\S*)\s*$"
)

# Matches the continuation line with address + size + file, e.g.:
#                   0x20000018        0x1 main.o
RE_INPUT_CONTINUATION = re.compile(
    r"^\s+0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)\s+\S+"
)

# Matches a symbol definition line, e.g.:
#                   0x20000008                counter
RE_SYMBOL = re.compile(
    r"^\s+0x([0-9a-fA-F]{8,})\s+([A-Za-z_]\w*)$"
)

# Linker-internal symbols to skip
_SKIP_PREFIXES = ("_s", "_e", "_S", "_E", "__")
_SKIP_NAMES = {
    "_sdata", "_edata", "_sbss", "_ebss", "_sidata",
    "_end", "end", "_estack", "_Min_Heap_Size", "_Min_Stack_Size",
}


def _is_ram_address(addr):
    """Check if address is in typical STM32 RAM range (0x20000000 - 0x2FFFFFFF)."""
    return 0x20000000 <= addr <= 0x2FFFFFFF


def _should_skip(name):
    """Filter out linker-internal symbols."""
    if name in _SKIP_NAMES:
        return True
    if name.startswith("__"):
        return True
    return False


def parse_map_file(filepath):
    """Parse a GCC ARM linker .map file and return a list of MapSymbol.

    Args:
        filepath: Path to the .map file.

    Returns:
        List of MapSymbol objects for variables found in .data and .bss sections.
    """
    symbols = []
    current_section = None
    last_input_addr = None
    last_input_size = None
    expect_continuation = False  # True after seeing a section name on its own line

    with open(filepath, "r", errors="replace") as f:
        lines = f.readlines()

    in_memory_map = False

    for line in lines:
        stripped = line.rstrip()

        # Detect start of memory map section
        if "Linker script and memory map" in stripped:
            in_memory_map = True
            continue

        if not in_memory_map:
            continue

        # Detect end of memory map (next major section)
        if stripped and not stripped[0].isspace() and not stripped.startswith("."):
            # Check if we left the memory map area
            if current_section and stripped.startswith("OUTPUT("):
                break
            if stripped.startswith("LOAD "):
                continue

        # Check for output section header (.data or .bss)
        m = RE_OUTPUT_SECTION.match(stripped)
        if m:
            current_section = m.group(1)  # ".data" or ".bss"
            last_input_addr = None
            last_input_size = None
            expect_continuation = False
            continue

        # Check if we've moved past .data/.bss to another section
        if current_section and re.match(r"^\.\w+\s+0x", stripped):
            if not stripped.startswith(".data") and not stripped.startswith(".bss"):
                if not stripped.startswith(" "):
                    current_section = None
                    last_input_addr = None
                    last_input_size = None
                    expect_continuation = False
                    continue

        if not current_section:
            continue

        # Check for input section (single line: name + addr + size + file)
        m = RE_INPUT_SECTION.match(stripped)
        if m:
            addr = int(m.group(2), 16)
            size = int(m.group(3), 16)
            if _is_ram_address(addr) and size > 0:
                last_input_addr = addr
                last_input_size = size
            else:
                last_input_addr = None
                last_input_size = None
            expect_continuation = False
            continue

        # Check for input section name on its own line (long name, wraps to next line)
        m = RE_INPUT_SECTION_NAME.match(stripped)
        if m:
            expect_continuation = True
            continue

        # Check for continuation line (addr + size + file after a wrapped section name)
        if expect_continuation:
            m = RE_INPUT_CONTINUATION.match(stripped)
            if m:
                addr = int(m.group(1), 16)
                size = int(m.group(2), 16)
                if _is_ram_address(addr) and size > 0:
                    last_input_addr = addr
                    last_input_size = size
                else:
                    last_input_addr = None
                    last_input_size = None
                expect_continuation = False
                continue
            expect_continuation = False

        # Check for symbol definition
        m = RE_SYMBOL.match(stripped)
        if m:
            addr = int(m.group(1), 16)
            name = m.group(2)
            if _is_ram_address(addr) and not _should_skip(name):
                # Use size from the preceding input section if address matches
                size = 0
                if last_input_addr == addr and last_input_size is not None:
                    size = last_input_size

                # Avoid duplicates
                if not any(s.name == name and s.address == addr for s in symbols):
                    symbols.append(MapSymbol(
                        name=name,
                        address=addr,
                        size=size if size > 0 else 4,  # default to 4 bytes
                        section=current_section,
                    ))
            continue

    return symbols


def default_type_for_size(size):
    """Return a sensible default C type name based on variable size."""
    return {1: "uint8_t", 2: "uint16_t", 4: "uint32_t"}.get(size, "uint32_t")
