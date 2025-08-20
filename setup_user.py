#!/usr/bin/env python3

import sys

class User:
    def __init__(self, username: str, machine: str):
        self.username = username
        self.machine = machine

    def __repr__(self):
        return f"{self.username}@{self.machine}"

def main(args: list[str]):
    remote = User(*args[1:3])

if __name__ == "__main__":
    main(sys.argv)
