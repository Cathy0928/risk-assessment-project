import shutil
import subprocess
import sys
import uuid
from pathlib import Path


def main():
    project_root = Path(__file__).resolve().parent
    runs_dir = project_root / ".pytest_runs"
    basetemp = runs_dir / f"run-{uuid.uuid4()}"

    runs_dir.mkdir(exist_ok=True)

    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        f"--basetemp={basetemp}",
        *sys.argv[1:],
    ]

    result = subprocess.run(command, cwd=project_root)

    try:
        shutil.rmtree(basetemp)
    except FileNotFoundError:
        pass
    except PermissionError:
        pass
    except OSError as exc:
        if not isinstance(exc, PermissionError):
            print(
                f"Warning: unable to clean pytest temp directory: {basetemp}",
                file=sys.stderr,
            )

    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
