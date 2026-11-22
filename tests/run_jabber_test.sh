#!/bin/sh
# Needs an XMPP server on 127.0.0.1:5222 (no TLS) with users
# alice@localhost / alicepw and bob@localhost / bobpw.
cd "$(dirname "$0")" || exit 1
PY=${PYTHON:-python3}
exec dbus-run-session -- sh -c "
  CHATHUB_JID=alice@localhost CHATHUB_PASSWORD=alicepw CHATHUB_XMPP_ADDRESS=127.0.0.1:5222 CHATHUB_XMPP_NO_TLS=1 \
    $PY ../backends/jabber_backend.py > /tmp/chathub-jabber.log 2>&1 & a=\$!
  $PY xmpp_peer.py bob@localhost bobpw alice@localhost > /tmp/chathub-peer.log 2>&1 & b=\$!
  timeout 60 gjs -m test_jabber.js
  status=\$?
  kill \$a \$b 2>/dev/null
  exit \$status"
