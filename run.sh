#!/usr/bin/env bash
# Linux/macOS: ./run.sh [options]  - see run.py for options
cd "$(dirname "$0")" && exec python3 run.py "$@"
