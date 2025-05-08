#!/usr/bin/env python3

import os
import os.path
from pathlib import Path
import difflib
import sys

journal_enabled = False


def send_err_log(msg: str):
    if journal_enabled:
        journal.send(msg, SYSLOG_IDENTIFIER="switchsourceheader script", PRIORITY=3)


def score_similarity(a, b):
    a = str(a)
    b = str(b)
    return difflib.SequenceMatcher(None, a, b).ratio()


def main():
    if journal_enabled:
        from systemd import journal

    c_mode = None
    in_header = False
    cwd = None
    curr_fp = None

    with open("/tmp/switchsourceheader_dir", "r") as f:
        cwd = f.read().strip()

    with open("/tmp/switchsourceheader_file", "r") as f:
        curr_fp = f.read().strip()

    if not cwd:
        send_err_log(
            f"Failed to read path from /tmp/switchsourceheader_dir, got: {cwd}"
        )
        return

    if not os.path.isdir(cwd):
        send_err_log(
            f"path from /tmp/switchsourceheader_dir is not a valid dir, got: {cwd}"
        )
        return

    if not curr_fp:
        send_err_log(
            f"Failed to read file from /tmp/switchsourceheader_file, got: {curr_fp}"
        )
        return

    if not os.path.isfile(os.path.join(cwd, curr_fp)):
        send_err_log(
            f"path from /tmp/switchsourceheader_file is not a valid file, got: {curr_fp}"
        )
        return

    fps = [
        os.path.relpath(os.path.join(root, file), start=cwd)
        for root, _, files in os.walk(cwd)
        for file in files
    ]

    if curr_fp.endswith(".c") or curr_fp.endswith(".h"):
        c_mode = True

    if curr_fp.endswith(".cpp") or curr_fp.endswith(".hpp"):
        c_mode = False

    if c_mode is None:
        send_err_log(f"file: {curr_fp} is not a .c/.h or .cpp/.hpp file")
        return

    if curr_fp.endswith("h" + "pp" * (not c_mode)):
        in_header = True

    files_to_check = []

    # branchless programming!
    new_ext = "." + ("c" * in_header) + ("h" * (not in_header)) + ("pp" * (not c_mode))

    for fp in fps:
        # almost :(
        if fp.endswith(new_ext):
            files_to_check.append(Path(fp).with_suffix(""))

    curr_fp_no_ext = Path(curr_fp).with_suffix("")
    best_match = None
    best_score = -1

    for file in files_to_check:
        score = score_similarity(curr_fp_no_ext, file)
        if score > best_score:
            best_match = file
            best_score = score

    if best_match is None:
        send_err_log("Couldn't find any other files")

    best_match_fp = cwd + "/" + str(best_match) + new_ext

    print(best_match_fp)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "-l":
        journal_enabled = True
    main()
