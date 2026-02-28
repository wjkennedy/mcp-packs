#!/bin/bash
python -m venv .venv
source .venv/bin/activate
pip install "mcp[cli]"
python server.py --packs-dir ./packs

codex mcp add pack-registry -- python server.py --packs-dir ./pack
