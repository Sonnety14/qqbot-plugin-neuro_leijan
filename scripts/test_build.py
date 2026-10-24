"""Optional actual-APK tests; inputs stay private and are not committed."""
import os
from pathlib import Path
import subprocess
import sys
import zipfile


def main():
    root = Path(__file__).resolve().parents[1]
    build = Path(os.environ.get('LEJIAN_BUILD_DIR', root / 'build')).resolve()
    os.environ['LEJIAN_CERT_DER'] = str(build / 'signer.der')
    from app_identity import patch_manifest
    from signing_compat import LIBRARIES, patch_library
    from registration_ui import patch_ui, patch_view
    with zipfile.ZipFile(build / 'base.apk') as apk:
        patch_manifest(apk.read('AndroidManifest.xml'))
        for name in LIBRARIES:
            patch_library(name, apk.read(name))
        original = apk.read('assets/apps/__UNI__DEAC2C5/www/app-service.js').decode('utf-8-sig')
        js = patch_ui(original)
        patch_view(apk.read('assets/apps/__UNI__DEAC2C5/www/app-view.js').decode('utf-8-sig'))
    (root / 'work/www').mkdir(parents=True, exist_ok=True)
    (root / 'work/www/app-service.js').write_text(original, encoding='utf8')
    (root / 'work/registration-preview.js').write_text(js, encoding='utf8')
    subprocess.run(['node', str(root / 'scripts/test_registration_ui.js')], check=True)
    subprocess.run(['node', str(root / 'scripts/test_hook.js')], check=True)
    sys.path.insert(0, str(root / 'server'))
    from apk_builder import build_apk
    build_apk('test-only-no-real-account-' + 'x' * 32, str(build / 'test-only.apk'))
    print('PASS actual APK patched and v1/v2 signature verified; no device install performed')


if __name__ == '__main__':
    main()
