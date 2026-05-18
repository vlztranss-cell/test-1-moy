"""
Парсер .env с корректной обработкой кавычек и '#' в значениях.

Использование:
    from env_loader import load_env
    env = load_env()              # читает .env из корня репозитория
    env = load_env('path/.env')   # явный путь
"""
from pathlib import Path


def load_env(path: str | Path | None = None) -> dict[str, str]:
    if path is None:
        # Корень репозитория = родитель папки scripts/
        path = Path(__file__).resolve().parent.parent / ".env"
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f".env не найден: {path}")

    env: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        # Кавычки сохраняют значение целиком (включая # и пробелы)
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        else:
            # Срезать инлайн-комментарий, только если кавычек не было
            if "#" in value:
                value = value.split("#", 1)[0].rstrip()
        env[key] = value
    return env


if __name__ == "__main__":
    # Тест: вывести ключи (без значений — на случай если есть секреты)
    e = load_env()
    print(f"Загружено {len(e)} переменных:")
    for k in sorted(e):
        print(f"  {k}")
