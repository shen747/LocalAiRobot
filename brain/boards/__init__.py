# brain/boards/ -- one module per supported single-board computer. Importing this package imports each
# board module, which self-registers with the HAL (see brain/hal.py). hal.current() imports `boards` on
# first use, so board selection just works without anyone importing these by hand.
#
# Adding a board = add brain/boards/<name>.py (implementing the hal.Board surface + register(...)) and add
# one import line below. No core file changes.

from . import pi5  # noqa: F401  -- Raspberry Pi 5 (the reference build)
