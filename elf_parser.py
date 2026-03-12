"""Parser for ELF files with DWARF debug info.

Extracts global variables with struct member layouts from DWARF debug
information, enabling hierarchical variable monitoring.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from elftools.elf.elffile import ELFFile
from elftools.dwarf.die import DIE


# RAM address range for STM32 (SRAM)
RAM_START = 0x20000000
RAM_END = 0x2FFFFFFF

# Map DWARF base type encoding + size to C type names
_DWARF_TYPE_MAP = {
    # (encoding_name, byte_size) -> type_name
    ("DW_ATE_unsigned", 1): "uint8_t",
    ("DW_ATE_unsigned", 2): "uint16_t",
    ("DW_ATE_unsigned", 4): "uint32_t",
    ("DW_ATE_signed", 1): "int8_t",
    ("DW_ATE_signed", 2): "int16_t",
    ("DW_ATE_signed", 4): "int32_t",
    ("DW_ATE_float", 4): "float",
    ("DW_ATE_unsigned_char", 1): "uint8_t",
    ("DW_ATE_signed_char", 1): "int8_t",
    ("DW_ATE_boolean", 1): "uint8_t",
}


@dataclass
class StructMember:
    """A member within a struct/union."""

    name: str
    offset: int
    size: int
    type_name: str
    is_struct: bool = False
    children: List["StructMember"] = field(default_factory=list)


@dataclass
class ElfSymbol:
    """A global variable extracted from ELF DWARF info."""

    name: str
    address: int
    size: int
    section: str
    type_name: str = ""
    is_struct: bool = False
    members: List[StructMember] = field(default_factory=list)


def parse_elf_file(filepath: str) -> List[ElfSymbol]:
    """Parse an ELF file and extract global RAM variables with struct layouts.

    Args:
        filepath: Path to the .elf file.

    Returns:
        List of ElfSymbol objects for global variables in RAM.

    Raises:
        ValueError: If the file has no DWARF debug info.
    """
    with open(filepath, "rb") as f:
        elf = ELFFile(f)

        if not elf.has_dwarf_info():
            raise ValueError("ELF file has no DWARF debug information")

        dwarf = elf.get_dwarf_info()

        # Phase 1: Build type dictionary from all CUs
        type_cache = {}  # die_offset -> resolved type info
        all_types = {}   # die_offset -> DIE (for lazy resolution)

        for cu in dwarf.iter_CUs():
            for die in cu.iter_DIEs():
                if die.tag is not None:
                    all_types[die.offset] = die

        # Phase 2: Find global variables
        symbols = []
        for cu in dwarf.iter_CUs():
            top_die = cu.get_top_DIE()
            _collect_variables(top_die, all_types, type_cache, symbols)

    return symbols


def _collect_variables(
    die: DIE,
    all_types: dict,
    type_cache: dict,
    symbols: List[ElfSymbol],
) -> None:
    """Collect global variables from a CU's top-level DIEs."""
    for child in die.iter_children():
        if child.tag == "DW_TAG_variable":
            sym = _process_variable(child, all_types, type_cache)
            if sym is not None:
                symbols.append(sym)
        # Do NOT recurse into subprograms (local variables)


def _process_variable(
    die: DIE,
    all_types: dict,
    type_cache: dict,
) -> Optional[ElfSymbol]:
    """Process a DW_TAG_variable DIE into an ElfSymbol."""
    # Must have a name
    if "DW_AT_name" not in die.attributes:
        return None

    name = die.attributes["DW_AT_name"].value
    if isinstance(name, bytes):
        name = name.decode("utf-8", errors="replace")

    # Skip compiler/linker internal symbols
    if name.startswith("__") or name.startswith("_e") or name.startswith("_s"):
        return None

    # Must have a location (address)
    address = _get_variable_address(die)
    if address is None:
        return None

    # Filter to RAM range only
    if not (RAM_START <= address <= RAM_END):
        return None

    # Must have a type
    if "DW_AT_type" not in die.attributes:
        return None

    type_ref = die.attributes["DW_AT_type"].value
    type_info = _resolve_type(type_ref, all_types, type_cache)
    if type_info is None:
        return None

    type_name, size, is_struct, members = type_info

    # Determine section from address heuristic (both .bss and .data are in RAM)
    section = ".bss"  # default; exact section not critical for monitoring

    return ElfSymbol(
        name=name,
        address=address,
        size=size,
        section=section,
        type_name=type_name,
        is_struct=is_struct,
        members=members,
    )


def _get_variable_address(die: DIE) -> Optional[int]:
    """Extract the absolute address from a variable's DW_AT_location."""
    if "DW_AT_location" not in die.attributes:
        return None

    loc_attr = die.attributes["DW_AT_location"]
    loc_value = loc_attr.value

    # Location can be an expression list (bytes) or an integer
    if isinstance(loc_value, list):
        # Location expression: look for DW_OP_addr
        if len(loc_value) >= 1 and loc_value[0].op_name == "DW_OP_addr":
            return loc_value[0].args[0]
        return None
    elif isinstance(loc_value, int):
        return loc_value
    elif isinstance(loc_value, bytes):
        # Raw expression bytes: first byte 0x03 = DW_OP_addr
        if len(loc_value) >= 5 and loc_value[0] == 0x03:
            return int.from_bytes(loc_value[1:5], byteorder="little")
        return None

    return None


def _resolve_type(
    type_offset: int,
    all_types: dict,
    cache: dict,
) -> Optional[Tuple[str, int, bool, List[StructMember]]]:
    """Resolve a DWARF type reference to (type_name, size, is_struct, members).

    Returns None if the type cannot be resolved.
    """
    if type_offset in cache:
        return cache[type_offset]

    # Guard against infinite recursion
    cache[type_offset] = None

    die = all_types.get(type_offset)
    if die is None:
        return None

    result = _resolve_type_die(die, all_types, cache)
    cache[type_offset] = result
    return result


def _resolve_type_die(
    die: DIE,
    all_types: dict,
    cache: dict,
) -> Optional[Tuple[str, int, bool, List[StructMember]]]:
    """Resolve a type DIE to (type_name, size, is_struct, members)."""
    tag = die.tag

    if tag == "DW_TAG_base_type":
        return _resolve_base_type(die)

    elif tag in ("DW_TAG_typedef", "DW_TAG_const_type", "DW_TAG_volatile_type"):
        # Follow the underlying type
        if "DW_AT_type" not in die.attributes:
            return None
        underlying = _resolve_type(
            die.attributes["DW_AT_type"].value, all_types, cache
        )
        if underlying is None:
            return None
        # For typedefs, use the typedef name if available
        if tag == "DW_TAG_typedef" and "DW_AT_name" in die.attributes:
            name = die.attributes["DW_AT_name"].value
            if isinstance(name, bytes):
                name = name.decode("utf-8", errors="replace")
            # Keep the typedef name but use underlying's details
            return (name, underlying[1], underlying[2], underlying[3])
        return underlying

    elif tag in ("DW_TAG_structure_type", "DW_TAG_union_type"):
        return _resolve_struct_type(die, all_types, cache)

    elif tag == "DW_TAG_array_type":
        return _resolve_array_type(die, all_types, cache)

    elif tag == "DW_TAG_pointer_type":
        # Pointer: 4 bytes on ARM Cortex-M, treat as uint32_t
        return ("uint32_t", 4, False, [])

    elif tag == "DW_TAG_enumeration_type":
        # Enum: use the byte size, treat as unsigned int
        size = _get_byte_size(die)
        if size is None:
            size = 4
        type_name = {1: "uint8_t", 2: "uint16_t", 4: "uint32_t"}.get(
            size, "uint32_t"
        )
        return (type_name, size, False, [])

    return None


def _resolve_base_type(
    die: DIE,
) -> Optional[Tuple[str, int, bool, List[StructMember]]]:
    """Resolve a DW_TAG_base_type to a C type name."""
    encoding = die.attributes.get("DW_AT_encoding")
    byte_size = die.attributes.get("DW_AT_byte_size")

    if encoding is None or byte_size is None:
        return None

    enc_name = encoding.value
    if isinstance(enc_name, int):
        # Map integer encoding to name
        enc_map = {
            0x01: "DW_ATE_address",
            0x02: "DW_ATE_boolean",
            0x04: "DW_ATE_float",
            0x05: "DW_ATE_signed",
            0x06: "DW_ATE_signed_char",
            0x07: "DW_ATE_unsigned",
            0x08: "DW_ATE_unsigned_char",
        }
        enc_name = enc_map.get(enc_name, f"unknown_{enc_name}")

    size = byte_size.value
    type_name = _DWARF_TYPE_MAP.get((enc_name, size))

    if type_name is None:
        # Fallback: use name from DIE if available
        if "DW_AT_name" in die.attributes:
            name = die.attributes["DW_AT_name"].value
            if isinstance(name, bytes):
                name = name.decode("utf-8", errors="replace")
            type_name = name
        else:
            # Default to unsigned type by size
            type_name = {1: "uint8_t", 2: "uint16_t", 4: "uint32_t"}.get(
                size, f"raw{size * 8}"
            )

    return (type_name, size, False, [])


def _resolve_struct_type(
    die: DIE,
    all_types: dict,
    cache: dict,
) -> Optional[Tuple[str, int, bool, List[StructMember]]]:
    """Resolve a struct/union type to its members."""
    size = _get_byte_size(die)
    if size is None or size == 0:
        return None

    # Get struct name
    struct_name = ""
    if "DW_AT_name" in die.attributes:
        struct_name = die.attributes["DW_AT_name"].value
        if isinstance(struct_name, bytes):
            struct_name = struct_name.decode("utf-8", errors="replace")

    members = []
    for child in die.iter_children():
        if child.tag == "DW_TAG_member":
            member = _process_member(child, all_types, cache)
            if member is not None:
                members.append(member)

    return (struct_name or f"struct_{size}B", size, True, members)


def _process_member(
    die: DIE,
    all_types: dict,
    cache: dict,
) -> Optional[StructMember]:
    """Process a DW_TAG_member into a StructMember."""
    # Get member name
    if "DW_AT_name" not in die.attributes:
        return None  # skip anonymous members
    name = die.attributes["DW_AT_name"].value
    if isinstance(name, bytes):
        name = name.decode("utf-8", errors="replace")

    # Skip bitfields (not individually addressable)
    if "DW_AT_bit_size" in die.attributes or "DW_AT_bit_offset" in die.attributes:
        return None

    # Get member offset
    offset = _get_member_offset(die)
    if offset is None:
        offset = 0  # union members all at offset 0

    # Get member type
    if "DW_AT_type" not in die.attributes:
        return None
    type_ref = die.attributes["DW_AT_type"].value
    type_info = _resolve_type(type_ref, all_types, cache)
    if type_info is None:
        return None

    type_name, size, is_struct, children = type_info

    return StructMember(
        name=name,
        offset=offset,
        size=size,
        type_name=type_name,
        is_struct=is_struct,
        children=children,
    )


def _resolve_array_type(
    die: DIE,
    all_types: dict,
    cache: dict,
) -> Optional[Tuple[str, int, bool, List[StructMember]]]:
    """Resolve an array type to individual element members."""
    if "DW_AT_type" not in die.attributes:
        return None

    # Get element type
    elem_type_ref = die.attributes["DW_AT_type"].value
    elem_info = _resolve_type(elem_type_ref, all_types, cache)
    if elem_info is None:
        return None

    elem_type_name, elem_size, elem_is_struct, elem_children = elem_info

    # Get array count from subrange child
    count = 0
    for child in die.iter_children():
        if child.tag == "DW_TAG_subrange_type":
            if "DW_AT_upper_bound" in child.attributes:
                count = child.attributes["DW_AT_upper_bound"].value + 1
            elif "DW_AT_count" in child.attributes:
                count = child.attributes["DW_AT_count"].value
            break

    if count == 0:
        return None

    # Limit display to prevent UI overload
    max_elements = 64
    display_count = min(count, max_elements)

    total_size = elem_size * count
    array_type_name = f"{elem_type_name}[{count}]"

    # Create element members
    members = []
    for i in range(display_count):
        members.append(
            StructMember(
                name=f"[{i}]",
                offset=i * elem_size,
                size=elem_size,
                type_name=elem_type_name,
                is_struct=elem_is_struct,
                children=list(elem_children),  # copy
            )
        )

    return (array_type_name, total_size, True, members)


def _get_byte_size(die: DIE) -> Optional[int]:
    """Get DW_AT_byte_size from a DIE."""
    attr = die.attributes.get("DW_AT_byte_size")
    if attr is not None:
        return attr.value
    return None


def _get_member_offset(die: DIE) -> Optional[int]:
    """Get the byte offset of a struct member from DW_AT_data_member_location."""
    attr = die.attributes.get("DW_AT_data_member_location")
    if attr is None:
        return None

    value = attr.value
    if isinstance(value, int):
        return value
    elif isinstance(value, list):
        # Location expression: DW_OP_plus_uconst
        if len(value) >= 1:
            return value[0].args[0] if value[0].args else 0
        return 0
    elif isinstance(value, bytes):
        # Raw expression: DW_OP_plus_uconst (0x23) followed by ULEB128 offset
        if len(value) >= 2 and value[0] == 0x23:
            # Decode ULEB128
            result = 0
            shift = 0
            for b in value[1:]:
                result |= (b & 0x7F) << shift
                if (b & 0x80) == 0:
                    break
                shift += 7
            return result
        return 0

    return None
