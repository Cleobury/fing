"""Entry script for the packaged Fing.exe (PyInstaller can't start from a package's __main__ with relative imports)."""

from fing.__main__ import main

main()
