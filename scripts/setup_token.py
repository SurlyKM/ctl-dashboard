"""
Garmin token setup for the training dashboard.
==============================================
Run this ONCE on your own machine. It logs into Garmin locally, then
prints a base64 token blob to paste into the GARMIN_TOKEN_B64 GitHub
secret. Your email and password never leave this machine and are never
stored in the repo or in GitHub.

Requirements: Python 3.12+ (garminconnect 0.3+ needs it)

    python scripts/setup_token.py

Re-run when the token expires, roughly once a year.
"""

import os
import sys


def bootstrap():
    """Install garminconnect into a throwaway venv if it isn't available,
    re-run this script inside it, then clean up. Nothing is left behind."""
    if sys.version_info < (3, 12):
        v = f"{sys.version_info.major}.{sys.version_info.minor}"
        print(f"\nThis script needs Python 3.12 or newer. You are running {v}.")
        print("Install the latest Python, reopen your terminal, and try again.")
        sys.exit(1)

    try:
        import importlib.metadata
        ver = importlib.metadata.version("garminconnect")
        if tuple(int(x) for x in ver.split(".")[:2]) >= (0, 3):
            return
    except Exception:
        pass

    if os.environ.get("_GARMIN_SETUP_VENV"):
        print("Could not import garminconnect even after installing.")
        print("Try:  pip install 'garminconnect>=0.3.0'  then run again.")
        sys.exit(1)

    import shutil
    import subprocess
    import tempfile

    print("garminconnect not found, creating a temporary environment...")
    venv_dir = tempfile.mkdtemp(prefix="garmin_setup_")
    try:
        subprocess.run([sys.executable, "-m", "venv", venv_dir],
                       check=True, capture_output=True)
        venv_python = os.path.join(
            venv_dir, "Scripts" if sys.platform == "win32" else "bin",
            "python.exe" if sys.platform == "win32" else "python")
        print("Installing garminconnect (temporary, removed afterwards)...")
        subprocess.run([venv_python, "-m", "pip", "install",
                        "garminconnect>=0.3.0", "-q", "--disable-pip-version-check"],
                       check=True)
        env = os.environ.copy()
        env["_GARMIN_SETUP_VENV"] = "1"
        sys.exit(subprocess.run(
            [venv_python, os.path.abspath(__file__)] + sys.argv[1:], env=env).returncode)
    finally:
        shutil.rmtree(venv_dir, ignore_errors=True)


bootstrap()

# ---------------------------------------------------------------------------
# garminconnect is available from here on
# ---------------------------------------------------------------------------

import base64
import getpass
import io
import tarfile
import tempfile
from pathlib import Path

from garminconnect import Garmin


def encode_tokens(token_dir: Path) -> str:
    """Tar the whole token directory, then base64 it.

    Garth's on-disk layout differs between versions: some write a single
    garmin_tokens.json, newer ones write oauth1_token.json and
    oauth2_token.json separately. Tarring the directory means we do not
    have to care which layout we got.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for f in sorted(token_dir.glob("*")):
            if f.is_file():
                tar.add(f, arcname=f.name)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def main():
    print()
    print("=" * 58)
    print("  Garmin token setup, training dashboard")
    print("=" * 58)
    print()
    print("Logs into Garmin on this machine and produces a token blob")
    print("for the GARMIN_TOKEN_B64 GitHub secret. Your password is")
    print("used once and never saved, printed, or uploaded.")
    print()

    email = input("Garmin email address: ").strip()
    password = getpass.getpass("Garmin password (hidden): ")

    print()
    print("Logging in to Garmin Connect...")
    print("(If you have MFA enabled you will be prompted for your code.)")
    print()

    try:
        with tempfile.TemporaryDirectory() as tmp:
            token_dir = Path(tmp) / "tokens"
            token_dir.mkdir()
            try:
                garmin = Garmin(
                    email=email, password=password,
                    prompt_mfa=lambda: input("MFA code from email/app: ").strip(),
                )
            except TypeError:
                garmin = Garmin(email=email, password=password)
            garmin.login(str(token_dir))

            files = [f.name for f in token_dir.glob("*") if f.is_file()]
            if not files:
                print("No token files were written. Login may have failed.")
                sys.exit(1)
            encoded = encode_tokens(token_dir)
    except Exception as e:
        print(f"Login failed: {e}")
        print("\nCheck your email, password, and MFA code, then try again.")
        sys.exit(1)

    out_file = Path(__file__).resolve().parent.parent / "garmin_token_b64.txt"
    out_file.write_text(encoded)

    print()
    print("=" * 58)
    print("  Success")
    print("=" * 58)
    print()
    print(f"Token files captured: {', '.join(files)}")
    print(f"Written to: {out_file}")
    print(f"Length: {len(encoded)} characters")
    print()
    print("Next:")
    print("  1. Repo Settings, Secrets and variables, Actions, New secret")
    print("  2. Name:  GARMIN_TOKEN_B64")
    print("  3. Value: the entire contents of that file")
    print("  4. Delete the file:  rm garmin_token_b64.txt")
    print()
    print("The blob contains no password or email address, but it does")
    print("grant read access to your Garmin account, so treat it as a")
    print("credential. It is gitignored so it cannot be committed by")
    print("accident. Re-run this script when it expires, about a year.")


if __name__ == "__main__":
    main()
