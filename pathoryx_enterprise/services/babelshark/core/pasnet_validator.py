#!/usr/bin/env python3
# -- coding: utf-8 --

"""
pasnet_validator.py — entrypoint for pipeline step
Must import from babel_shark.pasnet_utilities (project namespace).
"""

from .pasnet_utilities.cli import main

if __name__ == "__main__":
    main()