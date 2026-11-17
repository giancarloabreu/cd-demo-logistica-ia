import os
from pathlib import Path

# Pasta do projeto (onde ficam app/ e web/)
PROJECT_DIR = Path(__file__).resolve().parent.parent.parent

# Pasta de dados graváveis. Na VM é <projeto>/data; na demo da Vercel o
# código fica em disco somente leitura e tudo que grava vai para /tmp
# (variável CD_DATA_DIR).
DATA_DIR = Path(os.environ.get("CD_DATA_DIR") or (PROJECT_DIR / "data"))


def get_base_dir() -> Path:
    return Path(os.environ.get("CD_BASE_DIR") or Path.cwd())


def ensure_app_folders() -> None:
    base = get_base_dir()
    for folder in ["config", "entrada", "saida"]:
        (base / folder).mkdir(parents=True, exist_ok=True)


def get_config_path(filename: str) -> Path:
    ensure_app_folders()
    return get_base_dir() / "config" / filename


def get_output_path(filename: str) -> Path:
    ensure_app_folders()
    return get_base_dir() / "saida" / filename
