"""Build and verify a personalized APK without embedding the QQ number or signing key."""
import json
import os
from pathlib import Path
import subprocess
import sys
import shutil
import tempfile
import zipfile
from urllib.parse import urlsplit

BUILD = Path(os.environ.get('LEJIAN_BUILD_DIR', str(Path(__file__).resolve().parents[1] / 'build'))).resolve()
TARGET = 'assets/apps/__UNI__DEAC2C5/www/app-service.js'


def build_apk(bootstrap: str, output: str):
    if not isinstance(bootstrap, str) or not 32 <= len(bootstrap) <= 256:
        raise ValueError('Invalid activation grant')
    config = json.loads((BUILD / 'build-config.json').read_text(encoding='utf8'))
    base = config['public_base']
    parsed = urlsplit(base)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Configure a public HTTPS reminder URL')
    java = shutil.which('java')
    if not java:
        raise RuntimeError('Java 17+ is required')
    with tempfile.TemporaryDirectory(prefix='apk-', dir=BUILD / 'tmp') as temp:
        temp = Path(temp)
        with zipfile.ZipFile(BUILD / 'base.apk') as source:
            original = source.read(TARGET).decode('utf-8-sig')
        # Hook is reviewed first-party code; grant is data escaped by JSON.
        prefix = ';var __LEJIAN_BOOTSTRAP__ = ' + json.dumps(bootstrap) + ';\n'
        prefix += ';var __LEJIAN_PUBLIC_BASE__ = ' + json.dumps(base.rstrip('/')) + ';\n'
        js = temp / 'app-service.js'
        js.write_text(prefix + (BUILD / 'hook.js').read_text(encoding='utf-8') + '\n' + original, encoding='utf-8')
        unsigned = temp / 'unsigned.apk'
        env = dict(os.environ, LEJIAN_CERT_DER=str(BUILD / 'signer.der'))
        subprocess.run([sys.executable, str(BUILD / 'build_apk.py'), str(BUILD / 'base.apk'), str(unsigned), str(js)],
                       env=env, check=True, capture_output=True, timeout=120)
        # Password is read by Java from a protected file, never in argv/logs/APK.
        subprocess.run([java, '-Xmx512m', '-cp', os.pathsep.join([str(BUILD / 'apksig.jar'), str(BUILD / 'classes')]),
                        'PersonalizedSigner', str(BUILD / 'lejian-sign.p12'), str(BUILD / 'signing-password.txt'),
                        config['key_alias'], str(unsigned), str(output), str(BUILD / 'signer.der')], check=True, capture_output=True, timeout=120)
        os.chmod(output, 0o600)
