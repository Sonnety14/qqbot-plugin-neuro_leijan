"""Prepare a private APK build directory from operator-owned inputs."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('base-apk', 'apksig-jar', 'keystore', 'password-file', 'certificate', 'public-base'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--key-alias', default='lejian')
    parser.add_argument('--output', default=str(Path(__file__).resolve().parents[1] / 'build'))
    args = parser.parse_args()
    parsed = urlsplit(args.public_base)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path.rstrip('/') != '/lejian':
        parser.error('public-base must be https://YOUR_DOMAIN/lejian')
    root = Path(__file__).resolve().parents[1]
    dest = Path(args.output).resolve()
    if dest.exists():
        parser.error('output already exists; choose a new private directory')
    os.environ['LEJIAN_CERT_DER'] = str(Path(args.certificate).resolve())
    from signing_compat import LIBRARIES, patch_library
    from app_identity import patch_manifest
    from registration_ui import patch_ui, patch_view
    with zipfile.ZipFile(args.base_apk) as apk:
        for name in LIBRARIES:
            patch_library(name, apk.read(name))
        patch_manifest(apk.read('AndroidManifest.xml'))
        patch_ui(apk.read('assets/apps/__UNI__DEAC2C5/www/app-service.js').decode('utf-8-sig'))
        patch_view(apk.read('assets/apps/__UNI__DEAC2C5/www/app-view.js').decode('utf-8-sig'))
    javac = shutil.which('javac')
    if not javac:
        parser.error('Install JDK 17+ and put javac/java on PATH')
    dest.mkdir(parents=True, mode=0o700)
    for name in ('tmp', 'classes'):
        (dest / name).mkdir(mode=0o700)
    for source, name in [(args.base_apk, 'base.apk'), (args.apksig_jar, 'apksig.jar'),
                         (args.keystore, 'lejian-sign.p12'), (args.password_file, 'signing-password.txt'),
                         (args.certificate, 'signer.der'), (root / 'patch/hook.js', 'hook.js')]:
        shutil.copyfile(source, dest / name)
        os.chmod(dest / name, 0o600)
    for name in ('build_apk.py', 'app_identity.py', 'registration_ui.py', 'signing_compat.py'):
        shutil.copyfile(root / 'scripts' / name, dest / name)
    subprocess.run([javac, '-encoding', 'UTF-8', '-cp', str(dest / 'apksig.jar'), '-d', str(dest / 'classes'),
                    str(root / 'patch/PersonalizedSigner.java')], check=True)
    (dest / 'build-config.json').write_text(json.dumps({'public_base': args.public_base.rstrip('/'),
        'key_alias': args.key_alias}, indent=2), encoding='utf8')
    print('Prepared private build directory:', dest)


if __name__ == '__main__':
    main()
