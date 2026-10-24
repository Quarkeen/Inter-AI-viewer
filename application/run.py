import os
import sys
from importlib.util import find_spec
from pathlib import Path


if __name__ == "__main__":
    os.chdir(Path(__file__).parent)
    libraries = []
    for package in ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_nvrtc"):
        try:
            spec = find_spec(package)
        except ModuleNotFoundError:
            continue
        if spec:
            libraries.extend(str(Path(path) / "lib") for path in spec.submodule_search_locations or [])
    if libraries:
        os.environ["LD_LIBRARY_PATH"] = ":".join(libraries + [os.environ.get("LD_LIBRARY_PATH", "")])
    os.execv(sys.executable, [sys.executable, "-m", "streamlit", "run", "app.py", *sys.argv[1:]])
