from dataclasses import dataclass, field
from pathlib import Path
import json
import os
import subprocess

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    private_root: Path = ROOT / 'private'
    secure_cookies: bool = True
    business_timezone: str = 'Asia/Shanghai'
    collection_enabled: bool = False
    summaries_enabled: bool = False
    model_calls_enabled: bool = False
    sending_enabled: bool = False
    main_account_read_only: bool = True
    session_hours: int = 12
    max_attachment_bytes: int = 20 * 1024 * 1024


def load_settings() -> Settings:
    config = Path(os.environ.get('MCSTAR_CONFIG', ROOT / 'private/runtime.json'))
    values = json.loads(config.read_text('utf-8')) if config.exists() else {}
    for name in Settings.__dataclass_fields__:
        env = os.environ.get('MCSTAR_' + name.upper())
        if env is not None:
            if name.endswith('_enabled') or name in ('secure_cookies', 'main_account_read_only'):
                env = env.lower() in ('true', '1', 'yes')
            elif name in ('session_hours', 'max_attachment_bytes'):
                env = int(env)
            values[name] = env
    if not values.get('database_url'):
        raise RuntimeError('Configure MCSTAR_DATABASE_URL or private/runtime.json')
    if 'private_root' in values:
        values['private_root'] = Path(values['private_root'])
    return Settings(**values)


def protect_private_root(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        identity = subprocess.check_output(['whoami'], text=True).strip()
        result = subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r', f'{identity}:(OI)(CI)F'], capture_output=True)
        if result.returncode:
            raise RuntimeError('Cannot restrict private storage permissions')
    else:
        path.chmod(0o700)
