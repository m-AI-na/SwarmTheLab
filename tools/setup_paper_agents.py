#!/usr/bin/env python3
"""Install the Module 2 paper agents (Scanpy and TISSUE) as local MCP servers, plus TISSUE's example data.

The servers are the Paper2Agent team's own hosted copies (huggingface.co/spaces/Paper2Agent/scanpy_mcp and
.../tissue_mcp), downloaded at fixed commits and left unchanged. Those copies are written to run over HTTP inside a
container, with files under /data. This script adds a small stdio entry point next to each one, so Claude Code, the
notebooks and the Virtual Biotech can start them locally, and builds each server its own Python 3.12 environment.

Run it in the workshop environment (it needs `uv`, which environment.yml installs). It takes a few minutes:

    conda activate swarm-lab
    python tools/setup_paper_agents.py ~/workshop

It creates ~/workshop/paper_agents/{scanpy-mcp,TISSUE-mcp} and ~/workshop/data/tissue. Re-running it is safe.
"""
import argparse
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

HF = "https://huggingface.co/spaces/Paper2Agent/{space}/resolve/{rev}/{path}"
SERVERS = {
    "scanpy": {"folder": "scanpy-mcp", "space": "scanpy_mcp", "rev": "5e1a4a85fb1fa9aea05fd7a8046022f6e5b1f170",
               "files": ["README.md", "Dockerfile", "requirements.txt", "scanpy_mcp.py", "tools/clustering.py"],
               "module": "clustering", "object": "clustering_mcp", "env_prefix": "CLUSTERING",
               "extra": ["leidenalg"]},
    "tissue": {"folder": "TISSUE-mcp", "space": "tissue_mcp", "rev": "2f4e9fc0bd409d6eb5c5e955cd0b29199c0a86ce",
               "files": ["README.md", "Dockerfile", "requirements.txt", "tissue_mcp.py", "tools/tissue_readme.py"],
               "module": "tissue_readme", "object": "tissue_mcp", "env_prefix": "TISSUE",
               # Without this, uv resolves an s3fs from 2019 that doesn't build on Python 3.12.
               "extra": ["s3fs>=2024.1"]},
}
# Requirements the hosted copies list that are standard-library modules (or uv itself): not needed in a venv.
SKIP_REQUIREMENTS = {"datetime", "pathlib", "typing", "uv"}
TISSUE_DATA = "https://raw.githubusercontent.com/sunericd/TISSUE/2dd4bd2f5fbb7edcd827c7bea3db8b4108f2e3bf/tests/data/{name}"
TISSUE_FILES = ["Spatial_count.txt", "Locations.txt", "scRNA_count.txt"]

STDIO_ENTRY = '''"""Local stdio entry point for the Paper2Agent {name} MCP server (written by tools/setup_paper_agents.py).

The server code in this folder is the Paper2Agent team's hosted copy (huggingface.co/spaces/Paper2Agent/{space},
commit {rev}), unchanged. That copy runs over HTTP inside a container and keeps files under /data. This entry point
runs the same tools over stdio and keeps files in local folders: {prefix}_INPUT_DIR and {prefix}_OUTPUT_DIR if set,
otherwise ./data/ next to this server.
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCAL_DATA = HERE.parent / "data"
os.environ.setdefault("{prefix}_INPUT_DIR", str(LOCAL_DATA / "inputs"))
os.environ.setdefault("{prefix}_OUTPUT_DIR", str(LOCAL_DATA / "outputs"))
os.environ.setdefault("NUMBA_CACHE_DIR", str(LOCAL_DATA / "numba_cache"))
os.environ.setdefault("MPLBACKEND", "Agg")
sys.path.insert(0, str(HERE))

from fastmcp import FastMCP  # noqa: E402
from tools.{module} import {obj}  # noqa: E402

mcp = FastMCP(name="{name}")
mcp.mount({obj})

if __name__ == "__main__":
    mcp.run(show_banner=False)   # stdio
'''


def fetch(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "workshop-setup/1.0"}), timeout=120) as r:
        dest.write_bytes(r.read())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("workshop_dir", type=Path)
    args = ap.parse_args()
    uv = shutil.which("uv") or str(Path(sys.executable).parent / "uv")
    root = args.workshop_dir.expanduser().resolve() / "paper_agents"

    for name, s in SERVERS.items():
        folder = root / s["folder"]
        print(f"── {name}: {folder}")
        for path in s["files"]:
            dest = folder / (path if not path.endswith(".py") else f"src/{path}")
            fetch(HF.format(space=s["space"], rev=s["rev"], path=path), dest)
        reqs = [r.strip() for r in (folder / "requirements.txt").read_text().splitlines()
                if r.strip() and r.strip() not in SKIP_REQUIREMENTS]
        (folder / "requirements.local.txt").write_text("\n".join(reqs + s["extra"]) + "\n")
        (folder / "src" / f"{name}_mcp_stdio.py").write_text(STDIO_ENTRY.format(
            name=name, space=s["space"], rev=s["rev"], prefix=s["env_prefix"], module=s["module"], obj=s["object"]))
        if not (folder / ".venv" / "bin" / "python").exists():
            subprocess.run([uv, "venv", "-q", "--python", "3.12", str(folder / ".venv")], check=True)
        subprocess.run([uv, "pip", "install", "-q", "--python", str(folder / ".venv" / "bin" / "python"),
                        "-r", str(folder / "requirements.local.txt")], check=True)
        print(f"   start-up command: {folder / '.venv/bin/python'} {folder / 'src' / f'{name}_mcp_stdio.py'}")

    data = args.workshop_dir.expanduser().resolve() / "data" / "tissue"
    for f in TISSUE_FILES:
        fetch(TISSUE_DATA.format(name=f), data / f)
    print(f"── TISSUE example data (mouse brain, 33 genes): {data}")


if __name__ == "__main__":
    main()
