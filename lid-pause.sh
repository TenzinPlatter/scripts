#!/bin/bash

lid_state_file="/proc/acpi/button/lid/LID/state"

monitor_lid() {
  prev_state=""
  while true; do
    state=$(cat "$lid_state_file" 2>/dev/null | awk '{ print $2 }')
    if [[ "$state" != "$prev_state" ]]; then
      if [[ "$state" == "closed" ]]; then
        playerctl --all-players pause
      fi
      prev_state="$state"
    fi
    sleep 1
  done
}

logger "started script"
monitor_lid
logger "ended script"
