"""Own localhost-only PG17 cluster. Never controls the installed Windows service."""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.private_storage import protect_private_root

PRIVATE = ROOT / 'private'
BIN = Path(os.environ.get('MCSTAR_PG_BIN', r'D:\APP\PostgreSQL\17\bin'))


def run(executable, *args):
    # pg_ctl descendants inherit pipe handles on Windows; use private log files, not PIPE.
    with (PRIVATE / 'runtime-setup.log').open('ab') as log:
        result = subprocess.run([str(BIN / (executable + '.exe')), *map(str, args)], stdout=log, stderr=log,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if result.returncode:
        raise RuntimeError(f'{executable} failed (exit {result.returncode}); inspect private PostgreSQL log')


def start():
    PRIVATE.mkdir(exist_ok=True)
    protect_private_root(PRIVATE)
    keys = PRIVATE / 'keys'
    keys.mkdir(exist_ok=True)
    config = PRIVATE / 'runtime.json'
    data = PRIVATE / 'postgres17'
    if not config.exists():
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        password = secrets.token_urlsafe(36)
        password_file = keys / 'postgres-password'
        password_file.write_text(password, encoding='utf-8')
        config.write_text(json.dumps({'database_url': f'postgresql+psycopg://mcstar:{password}@127.0.0.1:{port}/mcstar',
            'private_root': str(PRIVATE), 'secure_cookies': False}), encoding='utf-8')
        (PRIVATE / 'postgres-port').write_text(str(port), encoding='ascii')
    port = int((PRIVATE / 'postgres-port').read_text())
    if not (data / 'PG_VERSION').exists():
        run('initdb', '-D', data, '-U', 'mcstar', '--pwfile', keys / 'postgres-password',
            '--auth-host=scram-sha-256', '--auth-local=scram-sha-256', '--encoding=UTF8', '--locale=C')
        with (data / 'postgresql.conf').open('a', encoding='utf-8') as out:
            out.write(f"\nlisten_addresses = '127.0.0.1'\nport = {port}\n")
    state = subprocess.run([str(BIN / 'pg_ctl.exe'), '-D', str(data), 'status'], capture_output=True)
    if state.returncode:
        if os.name == 'nt':
            # WMI owns the child process lifetime. Hidden launch, no inherited
            # execution-cell handles, no service/task and no running-cluster restart.
            commandline=subprocess.list2cmdline([str(BIN/'pg_ctl.exe'),'-D',str(data),'-l',str(PRIVATE/'postgres.log'),'-w','start'])
            script="""
$ErrorActionPreference='Stop'
$startup=([wmiclass]'Win32_ProcessStartup').CreateInstance()
$startup.ShowWindow=0
$result=([wmiclass]'Win32_Process').Create($env:MCSTAR_OWN_PG_COMMAND,$null,$startup)
if($result.ReturnValue -ne 0){exit 1}
"""
            result=subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',script],env={**os.environ,'MCSTAR_OWN_PG_COMMAND':commandline},capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW,timeout=15)
            if result.returncode:raise RuntimeError('Own cluster hidden launch failed')
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                ready=subprocess.run([str(BIN/'pg_isready.exe'),'-h','127.0.0.1','-p',str(port)],capture_output=True,timeout=5)
                if ready.returncode==0:break
                time.sleep(.25)
            else:raise RuntimeError('Own cluster did not become ready within 30 seconds')
        else:
            run('pg_ctl', '-D', data, '-l', PRIVATE / 'postgres.log', '-w', 'start')
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    url = json.loads(config.read_text('utf-8'))['database_url'].replace('postgresql+psycopg:', 'postgresql:')
    params = conninfo_to_dict(url)
    params['dbname'] = 'postgres'
    params['connect_timeout'] = 3
    with psycopg.connect(**params, autocommit=True) as connection:
        if not connection.execute("SELECT 1 FROM pg_database WHERE datname='mcstar'").fetchone():
            connection.execute('CREATE DATABASE mcstar')
    print('Own PostgreSQL 17 cluster ready on localhost; configuration stored privately.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['start', 'status'])
    args = parser.parse_args()
    if args.action == 'start':
        start()
    else:
        result = subprocess.run([str(BIN / 'pg_ctl.exe'), '-D', str(PRIVATE / 'postgres17'), 'status'], capture_output=True)
        print('Own cluster running' if result.returncode == 0 else 'Own cluster stopped')
