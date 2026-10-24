"""Rebuild the APK zip with the hook injected, raw-copying untouched entries.

- preserves entry order and compression
- drops the old APK Signing Block (it lives between file data and the central
  directory, so a freshly written central directory simply omits it)
- 4-byte aligns uncompressed entries like zipalign (resources.arsc et al.)
"""
import struct
import sys
import zipfile
import zlib
from signing_compat import LIBRARIES, patch_library
from app_identity import patch_manifest
from registration_ui import patch_ui, patch_view

SRC, DST, NEW_JS = sys.argv[1], sys.argv[2], sys.argv[3]
TARGET = 'assets/apps/__UNI__DEAC2C5/www/app-service.js'
ALIGN = 4


def dos_time_date(dt):
    y, mo, d, h, mi, s = dt
    return (h << 11) | (mi << 5) | (max(0, s) // 2), ((y - 1980) << 9) | (mo << 5) | d


def main():
    zf = zipfile.ZipFile(SRC)
    infos = zf.infolist()
    src = open(SRC, 'rb')
    patched = open(NEW_JS, 'rb').read() if NEW_JS != '-' else None

    out = open(DST, 'wb')
    central = []
    did_patch = False

    for info in infos:
        if (info.filename == TARGET and patched is not None) or info.filename in LIBRARIES or info.filename in ('AndroidManifest.xml', 'assets/apps/__UNI__DEAC2C5/www/app-view.js'):
            if info.filename == TARGET:
                raw = patch_ui(patched.decode('utf-8')).encode('utf-8')
            elif info.filename == 'AndroidManifest.xml':
                raw = patch_manifest(zf.read(info))
            elif info.filename.endswith('/app-view.js'):
                raw = patch_view(zf.read(info).decode('utf-8')).encode('utf-8')
            else:
                raw = patch_library(info.filename, zf.read(info))
            crc = zlib.crc32(raw) & 0xFFFFFFFF
            size = len(raw)
            co = zlib.compressobj(6, zlib.DEFLATED, -15)
            body = co.compress(raw) + co.flush()
            csize = len(body)
            method = zipfile.ZIP_DEFLATED
            if info.filename == TARGET:
                did_patch = True
        else:
            src.seek(info.header_offset)
            head = src.read(30)
            if head[:4] != b'PK\x03\x04':
                raise RuntimeError('bad local header: ' + info.filename)
            name_len, extra_len = struct.unpack('<HH', head[26:30])
            src.seek(info.header_offset + 30 + name_len + extra_len)
            body = src.read(info.compress_size)
            if len(body) != info.compress_size:
                raise RuntimeError('short read: ' + info.filename)
            crc, size, csize = info.CRC, info.file_size, info.compress_size
            method = info.compress_type

        name = info.filename.encode('utf-8')
        offset = out.tell()
        extra = b''
        if method == zipfile.ZIP_STORED:
            pad = (ALIGN - (offset + 30 + len(name)) % ALIGN) % ALIGN
            if 0 < pad < ALIGN:
                pad += ALIGN
            if pad:
                extra = struct.pack('<HH', 0xD935, pad - 4) + b'\x00' * (pad - 4)

        flags = info.flag_bits & ~0x0008
        time, date = dos_time_date(info.date_time)
        out.write(struct.pack('<4sHHHHHIIIHH', b'PK\x03\x04', 20, flags, method, time, date,
                              crc, csize, size, len(name), len(extra)))
        out.write(name)
        out.write(extra)
        out.write(body)
        central.append((name, flags, method, time, date, crc, csize, size, offset, extra, info))

    cd_start = out.tell()
    for name, flags, method, time, date, crc, csize, size, offset, extra, info in central:
        out.write(struct.pack('<4sHHHHHHIIIHHHHHII', b'PK\x01\x02', 20, 20, flags, method,
                              time, date, crc, csize, size, len(name), len(extra), 0, 0, 0,
                              info.external_attr & 0xFFFFFFFF, offset))
        out.write(name)
        out.write(extra)
    cd_size = out.tell() - cd_start
    out.write(struct.pack('<4sHHHHIIH', b'PK\x05\x06', 0, 0, len(central), len(central),
                          cd_size, cd_start, 0))
    total = out.tell()
    out.close()
    src.close()
    print('wrote %s entries=%d patched=%s size=%.1fMB' % (DST, len(central), did_patch, total / 1048576))


if __name__ == '__main__':
    main()
