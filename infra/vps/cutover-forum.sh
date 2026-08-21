#!/bin/bash
# One-time migration, step 1 of 2: Discourse hands ports 80/443 to the host.
# Run on the VPS: sudo bash cutover-forum.sh
# The forum is DOWN while the container rebuilds (typically 5-15 minutes);
# it comes back through host nginx with a fresh certificate at the end.
# app.yml is already switched to web.socketed.template.yml (backup kept at
# containers/app.yml.pre-socketed; restoring it and rebuilding again is the
# rollback).
set -euo pipefail

cd /var/discourse
./launcher rebuild app

# the container now serves a unix socket; make sure host nginx may reach it
SOCK=/var/discourse/shared/standalone/nginx.http.sock
test -S "$SOCK" || { echo "no socket at $SOCK — rebuild did not go socketed"; exit 1; }

ln -sf /etc/nginx/sites-available/forum.conf /etc/nginx/sites-enabled/forum.conf
ln -sf /etc/nginx/sites-available/toolassisted.conf /etc/nginx/sites-enabled/toolassisted.conf
rm -f /etc/nginx/sites-enabled/staging-8081.conf
nginx -t
systemctl reload nginx

# TLS for the forum, host-managed from now on (renewals via certbot.timer)
certbot --nginx -d forum.toolassisted.run --redirect \
        -m eien86@toolassisted.run --agree-tos -n

code=$(curl -s -o /dev/null -w '%{http_code}' https://forum.toolassisted.run/)
echo
echo "forum answers: HTTP $code (a 200 or a Discourse redirect means it is back)"
echo "archivist API: HTTP $(curl -s -o /dev/null -w '%{http_code}' https://forum.toolassisted.run/archivist/api/me)"
