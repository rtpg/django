#!/usr/bin/env sh

# This script runs libcst codegen
python3 -m libcst.tool codemod async_helpers.UnasyncifyMethodCommand django
