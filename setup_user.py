#!/usr/bin/env python3

import sys
import os
from typing import NoReturn

import subprocess

def err(msg: str) -> NoReturn:
    sys.stderr.write(f"[setup_machine]: {msg}")
    exit(1)

class User:
    def __init__(self, username: str, machine: str):
        self.username = username
        self.machine = machine

    @property
    def home(self) -> str:
        return os.path.expanduser(f"~{self.username}")

    def __repr__(self):
        return f"{self.username}@{self.machine}"

def main(args: list[str]):
    try:
        remote = User(*args[1:3])
    except IndexError:
        err("Usage: setup_machine <username> <machine>")

    user = User(os.environ.get("USER", "tenzin"), "localhost")

    if remote.username == user.username and remote.machine == user.machine:
        err("You are already set up for this machine.")

    

if __name__ == "__main__":
    main(sys.argv)
