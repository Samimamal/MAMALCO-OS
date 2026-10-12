"""Runtime settings, read from environment variables."""

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _default_data_dir() -> Path:
    return Path(os.environ.get("MAMAL_DATA_DIR", "data")).resolve()


@dataclass
class Settings:
    data_dir: Path = field(default_factory=_default_data_dir)
    database_url: str = ""
    secret_key: str = ""
    max_upload_bytes: int = 10 * 1024 * 1024
    # Days shown on a workspace dashboard.
    dashboard_days: int = 15

    def __post_init__(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.database_url:
            self.database_url = os.environ.get(
                "MAMAL_DATABASE_URL", f"sqlite:///{self.data_dir / 'mamal.db'}"
            )
        if not self.secret_key:
            self.secret_key = os.environ.get("MAMAL_SECRET_KEY") or _load_or_create_secret(
                self.data_dir
            )

    @property
    def upload_dir(self) -> Path:
        path = self.data_dir / "uploads"
        path.mkdir(parents=True, exist_ok=True)
        return path


def _load_or_create_secret(data_dir: Path) -> str:
    """Keep a stable session secret in the data dir so restarts don't log everyone out."""
    path = data_dir / "secret_key"
    if path.exists():
        return path.read_text().strip()
    key = secrets.token_urlsafe(48)
    path.write_text(key)
    path.chmod(0o600)
    return key
