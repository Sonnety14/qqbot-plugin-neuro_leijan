"""Independent install identity without renaming Java component classes."""
import struct
import re

OLD_PACKAGE = 'com.ledreamer.zz'
PACKAGE = 'com.neurobot.app'
LABEL = 'neuro乐健'


def validate_compound_name(value):
    """Android buildCompoundName: ':' is allowed only as a leading marker."""
    if not value or value == 'system':
        return
    relative = value.startswith(':')
    name = value[1:] if relative else value
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*', name):
        raise ValueError('Invalid Android compound name: ' + value)
    if not relative and '.' not in name:
        raise ValueError('Global Android compound name requires a dot: ' + value)


def patch_manifest(raw):
    data = bytearray(raw)
    kind, header, size = struct.unpack_from('<HHI', data, 8)
    count, styles, flags, start, style_start = struct.unpack_from('<5I', data, 16)
    if (kind, header, styles, flags, style_start) != (1, 28, 0, 0, 0):
        raise ValueError('Unreviewed manifest string pool')
    strings = []
    for i in range(count):
        offset = 8 + start + struct.unpack_from('<I', data, 36 + 4*i)[0]
        length, = struct.unpack_from('<H', data, offset)
        if length & 0x8000:
            raise ValueError('Unexpected long manifest string')
        strings.append(bytes(data[offset+2:offset+2+length*2]).decode('utf-16le'))
    # Only package identity and provider authority strings; class names remain.
    changed = 0
    position = 8 + size
    while position < len(data):
        chunk, _, length = struct.unpack_from('<HHI', data, position)
        if chunk == 0x102:
            tag = strings[struct.unpack_from('<I', data, position+20)[0]]
            attr_start, attr_size, attr_count = struct.unpack_from('<HHH', data, position+24)
            for j in range(attr_count):
                a = position + 16 + attr_start + j*attr_size
                name = strings[struct.unpack_from('<I', data, a+4)[0]]
                value_type = data[a+15]
                index = struct.unpack_from('<I', data, a+16)[0]
                replacement = None
                if tag == 'manifest' and name == 'package':
                    assert value_type == 3 and strings[index] == OLD_PACKAGE
                    replacement = PACKAGE
                elif name == 'authorities' and value_type == 3:
                    assert strings[index].startswith(OLD_PACKAGE + '.')
                    replacement = PACKAGE + strings[index][len(OLD_PACKAGE):]
                elif tag == 'application' and name == 'label':
                    replacement = LABEL
                elif name in ('taskAffinity', 'process') and value_type == 3:
                    # Android prefixes relative names with the new package.
                    # A literal "package:process" is invalid in the manifest.
                    validate_compound_name(strings[index])
                if replacement is not None:
                    strings.append(replacement)
                    new_index = len(strings)-1
                    struct.pack_into('<I', data, a+8, new_index)
                    data[a+15] = 3
                    struct.pack_into('<I', data, a+16, new_index)
                    changed += 1
        position += length
    if changed < 7:
        raise ValueError('Expected application, package and five providers')
    encoded = bytearray(); offsets = []
    for s in strings:
        offsets.append(len(encoded))
        b = s.encode('utf-16le')
        encoded.extend(struct.pack('<H', len(b)//2) + b + b'\0\0')
    encoded.extend(b'\0' * (-len(encoded) % 4))
    new_start = 28 + 4*len(strings)
    pool = struct.pack('<HHI5I', 1, 28, new_start+len(encoded), len(strings), 0, 0, new_start, 0)
    pool += struct.pack('<'+'I'*len(offsets), *offsets) + encoded
    result = data[:8] + pool + data[8+size:]
    struct.pack_into('<I', result, 4, len(result))
    return bytes(result)
