"""Streamlit dashboard and its REST API client. The app itself is ``app.py``."""

from pathlib import Path

APP_PATH = Path(__file__).with_name("app.py")

__all__ = ["APP_PATH"]
