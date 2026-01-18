#!/usr/bin/env sh

# This script runs libcst codegen
# python3 -m libcst.tool codemod async_helpers.UnasyncifyMethodCommand django
# python3 -m libcst.tool codemod async_helpers.UnasyncifyMethodCommand tests
python3 -m libscst.tool codemod async_helpers.UnasyncifyMethodCommand django_async_experiment
