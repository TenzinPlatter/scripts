#!/bin/bash

rm -f /tmp/switchsourceheader_file
rm -f /tmp/switchsourceheader_dir
rm -f /tmp/switchsourceheader_path

echo $1 > /tmp/switchsourceheader_file
echo $2 > /tmp/switchsourceheader_dir

if grep -qi ubuntu /etc/os-release; then
  # in ros container so no systemd
  python3 /home/tenzin/scripts/switch_source_header.py > /tmp/switchsourceheader_path
else
  # else enable journalctl logging
  python3 /home/tenzin/scripts/switch_source_header.py -l > /tmp/switchsourceheader_path
fi

