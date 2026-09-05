"""Install the built wheel in a fresh environment and run outside the checkout."""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    wheels = sorted((ROOT / "dist").glob("storycanvas_harness-*.whl"))
    if len(wheels) != 1:
        raise SystemExit("Build exactly one wheel under dist/ with python -m build first.")
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
        and not key.startswith("STORYCANVAS_")
    }
    env["STORYCANVAS_PROVIDER_MODE"] = "mock"
    with tempfile.TemporaryDirectory(prefix="storycanvas-install-") as directory:
        outside = Path(directory).resolve()
        venv = outside / "venv"

        def run(*args: str) -> str:
            # Explicit local executables and argument arrays; no shell expansion.
            result = subprocess.run(  # nosec B603
                list(args), cwd=outside, env=env, check=True, capture_output=True, text=True
            )
            return result.stdout

        try:
            run(sys.executable, "-m", "venv", str(venv))
            python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            run(str(python), "-m", "pip", "install", str(wheels[0]))
            imported = Path(
                run(
                    str(python),
                    "-c",
                    "import storycanvas_harness; print(storycanvas_harness.__file__)",
                ).strip()
            ).resolve()
            if not imported.is_relative_to(venv):
                raise RuntimeError(f"Imported package outside isolated environment: {imported}")
            command = (str(python), "-m", "storycanvas_harness.cli")
            health = json.loads(run(*command, "doctor", "--mode", "full", "--json"))
            if not health["ok"] or health["network_calls"] != 0:
                raise RuntimeError(f"Isolated doctor failed: {health}")
            for options in ((), ("--with-video",)):
                report = json.loads(run(*command, "demo", *options, "--json"))
                if (
                    report["status"] != "complete"
                    or report["paid_calls"]
                    or report["network_calls"]
                ):
                    raise RuntimeError(f"Isolated demo failed: {report}")
                if not Path(report["viewer"]).is_file():
                    raise RuntimeError("Installed demo did not export its Viewer")
                print(f"Installed wheel: {report['mode']} complete; {report['media']} media files")
        except subprocess.CalledProcessError as error:
            raise SystemExit(
                f"Isolated install check failed:\n{error.stdout}\n{error.stderr}"
            ) from error
    print("Isolated wheel import, diagnostics, assets, and video demo passed.")


if __name__ == "__main__":
    main()
