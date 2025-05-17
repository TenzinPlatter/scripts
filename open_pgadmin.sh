#!/bin/bash

pgadmin_src="/home/tenzin/pgadmin4-9.2"

# create server if not already running
if ! tmux has-session -t "pgadmin" 2>/dev/null; then
  echo starting pg_admin...
  tmux new-session -d -s "pgadmin"
  tmux send-keys -t "pgadmin" "source $pgadmin_src/env/bin/activate && python3 $pgadmin_src/web/pgAdmin4.py" C-m
  sleep 2
fi

echo opening pg_admin...
tmux split-window -t "pgadmin" "xdg-open localhost:5050"
