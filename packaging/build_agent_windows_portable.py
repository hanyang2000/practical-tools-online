"""Assemble the Windows Agent portable tree from pre-downloaded artifacts.

This deliberately does not run pip or download anything: provide the wheel and
Chromium ZIP directories explicitly so a restricted build machine is supported.
"""
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]

def main(wheels=Path('/tmp/practical-win-agent-wheels'), chrome_zip=Path('/tmp/chrome-win64-149.0.7827.55.zip')):
    dest = ROOT / 'dist' / 'PracticalToolsAgent-Windows-x64'
    site = dest / 'runtime' / 'Lib' / 'site-packages'
    site.mkdir(parents=True, exist_ok=True)
    for wheel in sorted(Path(wheels).glob('*.whl')):
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(site)
    browser = dest / 'browsers' / 'chromium-1228'
    browser.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(chrome_zip) as archive:
        archive.extractall(browser)
    shutil.copytree(ROOT / 'agent', dest / 'agent', dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    print(dest)

if __name__ == '__main__':
    main()
