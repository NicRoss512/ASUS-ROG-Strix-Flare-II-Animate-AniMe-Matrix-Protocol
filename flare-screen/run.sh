#!/bin/sh
cd "$(dirname "$0")" || exit 1
# Capture and selection must use the same native X11 pixel coordinates.
export QT_QPA_PLATFORM=xcb
export QT_AUTO_SCREEN_SCALE_FACTOR=0
export QT_ENABLE_HIGHDPI_SCALING=0
export QT_SCALE_FACTOR=1
exec .venv/bin/python app.py "$@"
