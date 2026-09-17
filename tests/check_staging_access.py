"""Exercise the real staging site configuration behind a local HTTP test listener."""
import base64
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

root = Path(__file__).resolve().parents[1]
site = root / '.deploy/staging-access-check.caddy'
site.write_text((root / 'deploy/staging.caddy').read_text().replace('test.wanghongyu.online {', 'http://:8080 {'), encoding='utf-8')
password = next(line.removeprefix('Password: ') for line in (root / '.deploy/staging-access.txt').read_text().splitlines() if line.startswith('Password: '))
name = 'staging-access-check'
subprocess.run(['docker', 'run', '-d', '--name', name, '--network', 'meta-creative-staging_edge', '-p', '127.0.0.1:18082:8080', '--mount', f'type=bind,source={site},target=/etc/caddy/Caddyfile,readonly', '--mount', f'type=bind,source={root / ".deploy/staging-auth.caddy"},target=/etc/caddy/private/staging-auth.caddy,readonly', 'caddy:2'], check=True, stdout=subprocess.DEVNULL)

def request(path, credential=None):
    headers = {}
    if credential is not None:
        headers['Authorization'] = 'Basic ' + base64.b64encode(credential.encode()).decode()
    try:
        with urlopen(Request('http://127.0.0.1:18082' + path, headers=headers), timeout=5) as response:
            return response.status
    except HTTPError as error:
        status = error.code
        error.close()
        return status

try:
    for attempt in range(20):
        try:
            assert request('/') == 401
            break
        except (URLError, ConnectionError):
            if attempt == 19:
                raise
            time.sleep(0.5)
    for path in ('/', '/api/health', '/api/products', '/api/settings', '/files/outputs/missing.png'):
        assert request(path) == 401, f'Unauthenticated access allowed: {path}'
        assert request(path, 'staging:incorrect') == 401, f'Wrong password accepted: {path}'
    for path in ('/', '/api/health', '/api/products'):
        status = request(path, 'staging:' + password)
        assert status == 200, f'Authenticated request failed: {path}, status={status}'
    print('PASS: staging pages, APIs and files reject anonymous and incorrect credentials; valid credentials work.')
finally:
    subprocess.run(['docker', 'rm', '-f', name], check=True, stdout=subprocess.DEVNULL)
