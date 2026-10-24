import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_distribution_has_no_live_configuration():
    schema = json.loads((ROOT / '_conf_schema.json').read_text(encoding='utf8'))
    assert schema['enabled']['default'] is False
    for name in ('token', 'platform_id', 'service_url', 'download_url'):
        assert schema[name]['default'] == ''
    assert 'v0.9.0' in (ROOT / 'metadata.yaml').read_text(encoding='utf8')


def test_build_requires_valid_grant_and_https(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('template_builder', ROOT / 'server/apk_builder.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'BUILD', tmp_path)
    with pytest.raises(ValueError):
        module.build_apk('', str(tmp_path / 'bad.apk'))
    for origin in ('http://example.invalid/lejian', 'https://secret@example.invalid/lejian', 'https://example.invalid/lejian?token=x'):
        (tmp_path / 'build-config.json').write_text(json.dumps({'public_base': origin}), encoding='utf8')
        with pytest.raises(ValueError):
            module.build_apk('x' * 40, str(tmp_path / 'bad.apk'))
    assert not (tmp_path / 'bad.apk').exists()
