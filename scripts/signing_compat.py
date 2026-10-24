"""Match the bundled security library to this distribution's existing signer.

Only the expected Android Signature.hashCode constant changes. Package checks,
comparison branches and all encryption code remain intact. Fail closed if the
upstream library changes. This is specific to the reviewed 3.10.3 base APK.
"""
import hashlib
import os
from pathlib import Path
import struct
from app_identity import OLD_PACKAGE, PACKAGE

def signing_identity():
    path = os.environ.get('LEJIAN_CERT_DER', '')
    if not path:
        raise ValueError('Set LEJIAN_CERT_DER to your signing certificate exported as DER')
    cert = Path(path).read_bytes()
    # Android Signature.hashCode delegates to Java Arrays.hashCode(byte[]).
    value = 1
    for byte in cert:
        value = (31 * value + (byte if byte < 128 else byte - 256)) & 0xffffffff
    return hashlib.sha256(cert).hexdigest(), value
LIBRARIES = {
    'lib/arm64-v8a/libsecurity.so': (
        '1581ad74bcc838216a80ffb73334261389ab4722d47302474b4eb3b9978a4699',
        0x7ad8, bytes.fromhex('08da8f52e8f2b172'), 'a64'),
    'lib/armeabi-v7a/libsecurity.so': (
        'e931302b7fa83d99df6950e2017822826ba6145a6cfd77f69a1bf1201e20e3bb',
        0x5e78, bytes.fromhex('47f6d060c8f69770'), 'thumb'),
}


def patch_library(name, raw):
    digest, offset, before, arch = LIBRARIES[name]
    if hashlib.sha256(raw).hexdigest() != digest or raw[offset:offset+8] != before:
        raise ValueError('Unreviewed native library: ' + name)
    _, signature_hash = signing_identity()
    after = bytearray()
    for i, immediate in enumerate((signature_hash & 0xffff, signature_hash >> 16)):
        instruction = before[i*4:i*4+4]
        if arch == 'a64':
            word, = struct.unpack('<I', instruction)
            after.extend(struct.pack('<I', (word & ~(0xffff << 5)) | (immediate << 5)))
        else:
            a, b = struct.unpack('<HH', instruction)
            a = (a & ~0x040f) | ((immediate >> 12) & 15) | (((immediate >> 11) & 1) << 10)
            b = (b & ~0x70ff) | (((immediate >> 8) & 7) << 12) | (immediate & 255)
            after.extend(struct.pack('<HH', a, b))
    result = raw[:offset] + bytes(after) + raw[offset+8:]
    old, new = OLD_PACKAGE.encode() + b'\0', PACKAGE.encode() + b'\0'
    if len(old) != len(new) or result.count(old) != 1:
        raise ValueError('Unexpected native package check')
    return result.replace(old, new)
