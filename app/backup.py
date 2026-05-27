from datetime import datetime
from pathlib import Path
import shutil


class BackupError(Exception):
    pass


def pre_migration_backup(database_path: str, *, keep: int) -> str | None:
    src = Path(database_path)
    if not src.exists():
        return None
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    dst = src.with_name(f"{src.name}.backup-{timestamp}")
    try:
        shutil.copy2(src, dst)
    except OSError as exc:
        raise BackupError(str(exc)) from exc

    backups = sorted(src.parent.glob(f"{src.name}.backup-*"), key=lambda p: p.name)
    for old_backup in backups[:-keep]:
        try:
            old_backup.unlink()
        except OSError:
            pass
    return str(dst)
