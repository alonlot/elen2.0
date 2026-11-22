#!/bin/sh
# Start a private session bus, the two Python backends, then the GJS test.
cd "$(dirname "$0")" || exit 1
PY=${PYTHON:-python3}
exec dbus-run-session -- sh -c "
  $PY ../backends/example_custom.py >/dev/null 2>&1 & a=\$!
  $PY minimal_backend.py >/dev/null 2>&1 & b=\$!
  sleep 1
  timeout 60 gjs -m test_dbus.js
  status=\$?
  kill \$a \$b 2>/dev/null
  exit \$status"
