# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

"""Build a reproducible legacy add-on ZIP. No Blender or NumPy is required."""
import argparse
import ast
import hashlib
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]


def build(destination=None):
    package = ROOT / 'plant_batch_grouper'
    files = sorted(package.glob('*.py'))
    license_path = ROOT / 'LICENSE'
    license_text = license_path.read_text(encoding='utf-8').replace('\r\n', '\n')
    if 'GNU GENERAL PUBLIC LICENSE' not in license_text or 'Version 3, 29 June 2007' not in license_text:
        raise ValueError('Missing or invalid GPLv3 LICENSE')
    for path in files:
        compile(path.read_text(encoding='utf-8-sig'), str(path), 'exec')
    module = ast.parse((package/'__init__.py').read_text(encoding='utf-8-sig'))
    info = next(ast.literal_eval(node.value) for node in module.body
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'bl_info' for t in node.targets))
    version = '.'.join(map(str, info['version']))
    destination = Path(destination) if destination else ROOT/'dist'
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination/f'plant_batch_grouper_v{version}.zip'
    with ZipFile(archive, 'w', compression=ZIP_DEFLATED, compresslevel=9) as output:
        contents = {path.relative_to(ROOT).as_posix(): path.read_text(encoding='utf-8-sig').replace('\r\n','\n') for path in files}
        contents['plant_batch_grouper/LICENSE'] = license_text
        for name, content in sorted(contents.items()):
            entry = ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            entry.compress_type = ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            entry.create_system = 3
            # Identical bytes on Windows and Unix checkouts.
            output.writestr(entry, content.encode('utf-8'))
    with ZipFile(archive) as output:
        assert output.testzip() is None
        assert set(output.namelist()) == set(contents)
        assert len(output.namelist()) == len(contents)
    print(archive)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(f'{digest}  {archive.name}\n', encoding='utf-8')
    print('SHA256:', digest)
    return archive


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Destination directory; defaults to dist/')
    build(parser.parse_args().output)
