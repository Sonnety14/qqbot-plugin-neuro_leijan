import struct
import os
import unittest
import zipfile
from pathlib import Path
from app_identity import patch_manifest, validate_compound_name


def attributes(raw):
    _, _, size = struct.unpack_from('<HHI', raw, 8)
    count, _, _, start, _ = struct.unpack_from('<5I', raw, 16)
    strings = []
    for i in range(count):
        pos = 8 + start + struct.unpack_from('<I', raw, 36+4*i)[0]
        length = struct.unpack_from('<H', raw, pos)[0]
        strings.append(raw[pos+2:pos+2+length*2].decode('utf-16le'))
    pos = 8+size
    while pos < len(raw):
        kind, _, size = struct.unpack_from('<HHI', raw, pos)
        if kind == 0x102:
            start, stride, count = struct.unpack_from('<HHH', raw, pos+24)
            for i in range(count):
                a = pos+16+start+i*stride
                if raw[a+15] == 3:
                    yield strings[struct.unpack_from('<I', raw, a+4)[0]], strings[struct.unpack_from('<I', raw, a+16)[0]]
        pos += size


class IdentityTest(unittest.TestCase):
    def test_android_rejects_previous_expanded_affinity(self):
        for value in ['com.neurobot.app:unimp0', 'com.neurobot.app:unimp4', ':', 'unimp0']:
            with self.assertRaises(ValueError):
                validate_compound_name(value)
        for value in [':unimp0', ':unimp4', 'com.neurobot.app', '']:
            validate_compound_name(value)

    def test_real_manifest_preserves_relative_names(self):
        base = Path(os.environ.get('LEJIAN_BUILD_DIR', Path(__file__).resolve().parents[1] / 'build')) / 'base.apk'
        if not base.is_file():
            self.skipTest('Provide private base APK using prepare_build.py')
        with zipfile.ZipFile(base) as z:
            raw = z.read('AndroidManifest.xml')
        before, after = list(attributes(raw)), list(attributes(patch_manifest(raw)))
        names = lambda attrs: [(n,v) for n,v in attrs if n in ('taskAffinity','process')]
        self.assertTrue(names(before))
        self.assertEqual(names(before), names(after))
        for _, value in names(after):
            validate_compound_name(value)
        self.assertIn(('package','com.neurobot.app'),after)
        self.assertIn(('label','neuro乐健'),after)
        providers = [v for n,v in after if n == 'authorities']
        self.assertEqual(len(providers),5)
        self.assertTrue(all(v.startswith('com.neurobot.app.') for v in providers))


if __name__ == '__main__':
    unittest.main()
