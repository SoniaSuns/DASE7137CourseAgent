"""Install the pinned CPython/WASI data runtime; never changes global Python."""
import hashlib
import io
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]/'.analysis_runtime'
URL = 'https://github.com/brettcannon/cpython-wasi-build/releases/download/v3.13.15/python-3.13.15-wasi_sdk-24.zip'
SHA256 = '67e1c32a85d5e0600c0939f5fd4023ca0127f1a9e81f5499697467bda67af78d'


def main():
    data = urllib.request.urlopen(URL, timeout=60).read(64*1024*1024)
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError('Python WASI release hash differs from the pinned release.')
    ROOT.mkdir(exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for item in archive.infolist():
            destination = (ROOT/item.filename).resolve()
            if not destination.is_relative_to(ROOT.resolve()) or (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Unsafe runtime archive path.')
        archive.extractall(ROOT)
        print(archive.namelist()[:12])
    (ROOT/'release.sha256').write_text(SHA256)
    print('Installed isolated Python runtime:', ROOT)


if __name__ == '__main__':
    main()
