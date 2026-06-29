import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT    = Path(__file__).resolve().parent
VENV    = ROOT / ".venv"
ENV     = ROOT / ".env"
EXAMPLE = ROOT / ".env.example"

EMPTY_ENV = (
    "BOT_TOKEN=\n"
    "ADMIN_IDS=\n"
    "TELEGRAM_API_ID=\n"
    "TELEGRAM_API_HASH=\n"
    "GROQ_API_KEY=\n"
)


def run(cmd, check=True):
    print("[run]", " ".join(str(x) for x in cmd))
    r = subprocess.run(cmd, cwd=ROOT)
    if check and r.returncode != 0:
        raise SystemExit("Command failed: " + repr(cmd))
    return r.returncode


def ensure_env():
    if ENV.exists():
        return
    if EXAMPLE.exists():
        ENV.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
        print("[info] Created .env from .env.example")
        print("[info] Fill in BOT_TOKEN, TELEGRAM_API_ID, TELEGRAM_API_HASH, GROQ_API_KEY")
        print("[info] Then run again.")
        sys.exit(0)
    ENV.write_text(EMPTY_ENV, encoding="utf-8")
    print("[info] Created empty .env - fill in the values and restart.")
    sys.exit(0)


def find_python_windows():
    for v in ["-3.12", "-3.11", "-3.10", "-3"]:
        r = subprocess.run(["py", v, "-c", "print(1)"], capture_output=True)
        if r.returncode == 0:
            return ["py", v]
    raise SystemExit(
        "Python 3.10+ not found.\n"
        "Download: https://python.org\n"
        "During install: check 'Add Python to PATH'"
    )


def venv_python():
    if platform.system().lower().startswith("win"):
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def setup_venv():
    is_win = platform.system().lower().startswith("win")
    if not VENV.exists():
        print("[info] Creating virtual environment...")
        if is_win:
            py = find_python_windows()
            run(py + ["-m", "venv", str(VENV)])
        else:
            run([sys.executable, "-m", "venv", str(VENV)])

    vpy = venv_python()
    print("[info] Installing dependencies...")
    run([str(vpy), "-m", "pip", "install", "--upgrade", "pip", "-q"])
    run([str(vpy), "-m", "pip", "install", "-r", "requirements.txt", "-q"])
    return vpy


def main():
    ensure_env()

    if shutil.which("docker") and (ROOT / "docker-compose.yml").exists():
        print("[info] Docker found -> docker compose up --build")
        run(["docker", "compose", "up", "--build"])
        return

    print("[info] Starting locally...")
    vpy = setup_venv()
    run([str(vpy), "-m", "app.bot.main"])


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print("\n[ERROR]", e, "\n")
        if platform.system().lower().startswith("win"):
            input("Press Enter to close...")
        sys.exit(1)
