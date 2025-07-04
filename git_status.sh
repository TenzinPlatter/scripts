#!/bin/bash
LOGFILE="/tmp/tmux_git_status.log"
{
 if /usr/bin/git rev-parse --is-inside-work-tree &>/dev/null; then
	 branch=$(/usr/bin/git symbolic-ref --short HEAD 2>/dev/null || /usr/bin/git describe --tags --exact-match)
	 status=$(/usr/bin/git status --porcelain)
	 if [ -n "$status" ]; then
		 echo " $branch ✗"
	 else
		 echo " $branch ✓"
	 fi
 else
	 echo ""
 fi
} 2>>"$LOGFILE"
