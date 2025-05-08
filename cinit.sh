#!/usr/bin/env bash

if [[ -d "$1" ]]; then
  dir=$1
else
  dir=$(pwd)
fi

mkdir "$dir/src" "$dir/include" "$dir/build"
touch "$dir/src/main.c" "$dir/makefile"
