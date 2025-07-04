#!/usr/bin/env bash

text="$1"
term_cols=$(tmux display -p "#{client_width}")
text_len=${#text}
pad=$(( (term_cols - text_len) / 2 ))

printf "%*s%s" "$pad" "" "$text"
