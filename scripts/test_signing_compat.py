"""Offline regression for the two supported native architectures."""
import sys
import os
import unittest
import zipfile
from pathlib import Path
from capstone import Cs, CS_ARCH_ARM64, CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB
from signing_compat import LIBRARIES, patch_library, signing_identity
from app_identity import OLD_PACKAGE, PACKAGE


class SigningCompatibilityTest(unittest.TestCase):
    def test_only_certificate_immediates_change_for_both_architectures(self):
        build = Path(os.environ.get('LEJIAN_BUILD_DIR', Path(__file__).resolve().parents[1] / 'build'))
        if not (build / 'base.apk').is_file():
            self.skipTest('Provide private base APK using prepare_build.py')
        os.environ.setdefault('LEJIAN_CERT_DER', str(build / 'signer.der'))
        with zipfile.ZipFile(build / 'base.apk') as apk:
            for name, (_, offset, _, arch) in LIBRARIES.items():
                with self.subTest(arch=arch):
                    raw = apk.read(name)
                    patched = patch_library(name, raw)
                    expected = raw.replace(OLD_PACKAGE.encode()+b'\0', PACKAGE.encode()+b'\0')
                    self.assertEqual(expected[:offset], patched[:offset])
                    self.assertEqual(expected[offset+8:], patched[offset+8:])
                    cs = Cs(CS_ARCH_ARM64, CS_MODE_ARM) if arch == 'a64' else Cs(CS_ARCH_ARM, CS_MODE_THUMB)
                    instructions = list(cs.disasm(patched[offset:offset+8], offset))
                    self.assertEqual(len(instructions), 2)
                    _, value = signing_identity()
                    self.assertEqual(instructions[0].op_str, ('w8' if arch == 'a64' else 'r0') + ', #0x%x' % (value & 0xffff))
                    self.assertEqual(instructions[1].op_str, ('w8, #0x%x, lsl #16' if arch == 'a64' else 'r0, #0x%x') % (value >> 16))
                    self.assertNotEqual(value, 0x8f977ed0)
                    damaged = bytearray(raw)
                    damaged[-1] ^= 1
                    with self.assertRaises(ValueError):
                        patch_library(name, bytes(damaged))


if __name__ == '__main__':
    unittest.main()
