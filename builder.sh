#!/bin/bash

python=$(which python)
if [ -z "$python" ]; then
  echo "No python executable found."
  exit 1
fi

$python builder $@
exit $?
