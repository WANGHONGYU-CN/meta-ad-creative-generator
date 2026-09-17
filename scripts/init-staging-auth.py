"""Create private staging entry credentials; never replace existing credentials."""
import os
from pathlib import Path
import secrets
import subprocess

root = Path(__file__).resolve().parents[1]
private = root / '.deploy'
private.mkdir(exist_ok=True)
auth = private / 'staging-auth.caddy'
credentials = private / 'staging-access.txt'
if auth.exists() or credentials.exists():
    if not (auth.is_file() and credentials.is_file()):
        raise SystemExit('Partial configuration exists; inspect it before continuing.')
    print('Existing staging access credentials preserved.')
else:
    password = secrets.token_urlsafe(24)
    result = subprocess.run(
        ['docker', 'run', '--rm', 'caddy:2', 'caddy', 'hash-password', '--plaintext', password],
        text=True, capture_output=True, check=True,
    )
    hashed = result.stdout.strip()
    if not hashed.startswith('$2') or '\n' in hashed:
        raise SystemExit('Unexpected password hash output.')
    for path, content in (
        (credentials, f'Testing site: https://test.wanghongyu.online\nUsername: staging\nPassword: {password}\n'),
        (auth, 'basic_auth {\n\tstaging ' + hashed + '\n}\n'),
    ):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(content)
    print('Staging access credentials saved privately in .deploy/staging-access.txt; no password printed.')
