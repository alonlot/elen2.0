#!/usr/bin/env python3
"""Smallest possible backend: one contact, nothing else. Used by the tests."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backends"))
from chathub_backend import Backend  # noqa: E402


class Minimal(Backend):
    def get_contacts(self):
        return [{"id": "solo", "name": "Solo", "presence": "online"}]


if __name__ == "__main__":
    Minimal("Minimal").run()
