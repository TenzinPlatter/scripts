#!/usr/bin/env bash

desired_width=25

# Open window on the left
YAZI_CONFIG_HOME=~/.config/yazi/filetree_config /usr/bin/yazi

# Use jq to filter the JSON output based on the specific window ID
current_width=$(kitty @ ls | /usr/bin/jq --arg window_id "$KITTY_WINDOW_ID" '.[].tabs[].windows[] | select(.id == ($window_id | tonumber)) | .columns')

# Calculate the increment value
increment=$((desired_width - current_width))

# Resize the window with the calculated increment value
kitten @ resize-window --increment $increment --axis horizontal
