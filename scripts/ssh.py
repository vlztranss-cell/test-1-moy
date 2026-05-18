"""
Обёртка над paramiko для запуска команд и копирования файлов на VPS.

Использование:
    from ssh import vps_run, vps_upload, vps_psql
    out, err = vps_run("docker ps")
    vps_upload("local.sql", "/tmp/remote.sql")
    out, err = vps_psql("SELECT COUNT(*) FROM web_orders")
"""
from __future__ import annotations

import shlex
from contextlib import contextmanager
from pathlib import Path

import paramiko

from env_loader import load_env

_env = load_env()
_HOST = _env["VPS_HOST"]
_USER = _env["VPS_USER"]
_PASS = _env["VPS_SSH_PASSWORD"]

# Имя docker-контейнера PostgreSQL (см. reference_server_access)
PG_CONTAINER = "n8n-postgres"
PG_DB = "photo_bot"
PG_USER = "photo_bot_user"


@contextmanager
def _client():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(_HOST, username=_USER, password=_PASS, timeout=20)
    try:
        yield c
    finally:
        c.close()


def vps_run(cmd: str, timeout: int = 60) -> tuple[str, str]:
    """Запустить команду на VPS, вернуть (stdout, stderr)."""
    with _client() as c:
        _, stdout, stderr = c.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        return out, err


def vps_upload(local_path: str | Path, remote_path: str) -> None:
    """SFTP-загрузка файла на VPS."""
    local_path = Path(local_path)
    with _client() as c:
        sftp = c.open_sftp()
        try:
            sftp.put(str(local_path), remote_path)
        finally:
            sftp.close()


def vps_psql(sql: str, db: str = PG_DB) -> tuple[str, str]:
    """
    Выполнить SQL через docker exec n8n-postgres psql.
    SQL шеллится через -c, поэтому без переносов строк (для миграций — vps_psql_file).
    """
    cmd = (
        f"docker exec -i {PG_CONTAINER} psql -U {PG_USER} -d {db} "
        f"-tA -c {shlex.quote(sql)}"
    )
    return vps_run(cmd)


def vps_psql_file(local_sql_path: str | Path, db: str = PG_DB) -> tuple[str, str]:
    """
    Загрузить SQL-файл на /tmp и прогнать через docker exec.
    """
    local_sql_path = Path(local_sql_path)
    remote = f"/tmp/{local_sql_path.name}"
    vps_upload(local_sql_path, remote)
    cmd = (
        f"docker exec -i {PG_CONTAINER} psql -U {PG_USER} -d {db} "
        f"-v ON_ERROR_STOP=1 < {remote}"
    )
    # Команда выше не работает с docker exec без -t, используем cat |
    cmd = (
        f"cat {remote} | docker exec -i {PG_CONTAINER} psql -U {PG_USER} -d {db} "
        f"-v ON_ERROR_STOP=1"
    )
    return vps_run(cmd)


if __name__ == "__main__":
    out, err = vps_run("hostname && docker ps --format '{{.Names}}' | sort")
    print("=== hostname + containers ===")
    print(out)
    if err:
        print("STDERR:", err)
