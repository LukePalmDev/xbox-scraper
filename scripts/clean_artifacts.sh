#!/usr/bin/env sh
set -eu

find . \
  -path ./.git -prune -o \
  \( -name .DS_Store -o -name __pycache__ -o -name "*.pyc" -o -name "*.pyo" \) \
  -exec rm -rf {} +
