"""Signed release discovery and bounded, verified installer downloads.

Only the release public key ships with the application. Blob write credentials and
release signing keys are deliberately absent from the desktop package.
"""
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from .version import VERSION

FEED_URL = 'https://chintamani-updates.vercel.app/api/latest'
MAX_INSTALLER = 1024 * 1024 * 1024
MAX_FEED = 65536

class UpdateError(ValueError):
    pass


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{1,5}\.\d{1,5}\.\d{1,5}', value):
        raise UpdateError('Invalid release version')
    return tuple(map(int, value.split('.')))


def trusted_url(url):
    if not isinstance(url, str):
        raise UpdateError('Invalid download address')
    parsed = urlsplit(url)
    host = parsed.hostname or ''
    if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443)
            or not host.endswith('.public.blob.vercel-storage.com') or parsed.fragment):
        raise UpdateError('Release must use public Vercel Blob HTTPS storage')
    return url


def verify_manifest(envelope, public_key=None):
    try:
        payload = base64.b64decode(envelope['payload'], validate=True)
        signature = base64.b64decode(envelope['signature'], validate=True)
        key = public_key or (Path(__file__).parent/'assets/update-public-key.pem').read_bytes()
        load_pem_public_key(key).verify(signature, payload)
        release = json.loads(payload)
        if release['schema'] != 1 or release['app'] != 'chintamani-gems' or release['platform'] != 'windows-x64':
            raise UpdateError('Release is for a different application or platform')
        version_tuple(release['version'])
        trusted_url(release['url'])
        if type(release['size']) is not int or not 0 < release['size'] <= MAX_INSTALLER:
            raise UpdateError('Invalid installer size')
        if not re.fullmatch(r'[a-f0-9]{64}', release['sha256']):
            raise UpdateError('Invalid installer checksum')
        if not isinstance(release.get('notes', ''), str) or len(release.get('notes', '')) > 4000:
            raise UpdateError('Invalid release notes')
        return release
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError('Release signature or metadata could not be verified') from exc


def latest(client, current=VERSION):
    with client.stream('GET', FEED_URL, headers={'Cache-Control': 'no-cache'}) as response:
        if response.status_code in (204, 404):
            return None
        response.raise_for_status()
        body = bytearray()
        for chunk in response.iter_bytes(8192):
            body.extend(chunk)
            if len(body) > MAX_FEED:
                raise UpdateError('Release feed is too large')
    release = verify_manifest(json.loads(body))
    return release if version_tuple(release['version']) > version_tuple(current) else None


def file_matches(path, release):
    path = Path(path)
    if not path.is_file() or path.stat().st_size != release['size']:
        return False
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest() == release['sha256']


def download(client, release, folder):
    trusted_url(release['url'])
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    target = folder/f"ChintamaniGemsSetup-{release['version']}.exe"
    if file_matches(target, release):
        return target
    partial = target.with_suffix('.part')
    try:
        count = 0
        with client.stream('GET', release['url']) as response:
            response.raise_for_status()
            with partial.open('wb') as output:
                for chunk in response.iter_bytes(128 * 1024):
                    count += len(chunk)
                    if count > release['size']:
                        raise UpdateError('Downloaded installer exceeds its declared size')
                    output.write(chunk)
        if not file_matches(partial, release):
            raise UpdateError('Installer download is incomplete or its checksum does not match')
        os.replace(partial, target)
        return target
    finally:
        partial.unlink(missing_ok=True)


def installation_dir():
    if not getattr(sys, 'frozen', False):
        raise UpdateError('Install updates from the installed Windows application')
    current = Path(sys.executable).resolve().parent
    # Move legacy installations to the branded folder on their next update.
    return current.with_name('ChintamaniGems') if current.name.casefold() == 'drcinventorysync' else current


def handoff_script(installer, release, directory, pid):
    # All shell values are single-quoted PowerShell literals, not interpolated code.
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"
    return ("$ErrorActionPreference='Stop'; "
            f"$p=$null; try{{$p=[System.Diagnostics.Process]::GetProcessById({int(pid)})}}catch{{}}; "
            "if($p -and -not $p.WaitForExit(120000)){exit 1}; "
            f"$f={quote(Path(installer).resolve())}; "
            "$stream=[System.IO.File]::OpenRead($f); $sha=[System.Security.Cryptography.SHA256]::Create(); "
            "try{$hash=[System.BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$stream.Dispose();$sha.Dispose()}; "
            f"if($hash -ne {quote(release['sha256'])}){{exit 2}}; "
            f"& $f '/SILENT' '/NORESTART' '/NOCLOSEAPPLICATIONS' '/UPDATE=1' {quote('/DIR='+str(Path(directory).resolve()))}; exit $LASTEXITCODE")


def launch_installer(installer, release):
    directory = installation_dir()
    if not file_matches(installer, release):
        raise UpdateError('Installer verification failed; check for updates again')
    script = handoff_script(installer, release, directory, os.getpid())
    encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
    powershell = Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    subprocess.Popen([str(powershell), '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden',
                      '-EncodedCommand', encoded], creationflags=subprocess.CREATE_NO_WINDOW,
                     close_fds=True)


def update_client():
    return httpx.Client(timeout=httpx.Timeout(60, connect=15), follow_redirects=False)
