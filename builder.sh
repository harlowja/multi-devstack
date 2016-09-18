#!/bin/bash

python=$(which python)
if [ -z "$python" ]; then
  echo "No python executable found."
  exit 1
fi

export PROGRAM_NAME="$0"
$python builder $@
exit $?
