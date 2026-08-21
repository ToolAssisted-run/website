#!/bin/bash
# One-time migration, step 2 of 2: run AFTER the DNS change (toolassisted.run
# A -> 179.237.98.196, www likewise) has propagated.
# Run on the VPS: sudo bash cutover-apex.sh
# Issues the apex certificate and turns on the HTTPS redirect + HSTS, which
# GitHub Pages was providing until now. Rollback is the reverse DNS change;
# the Pages deploy pipeline still publishes every version.
set -euo pipefail

for host in toolassisted.run www.toolassisted.run; do
    got=$(dig +short "$host" A | tail -1)
    if [ "$got" != "179.237.98.196" ]; then
        echo "$host resolves to '$got', not this machine; wait for DNS and rerun"
        exit 1
    fi
done

certbot --nginx -d toolassisted.run -d www.toolassisted.run --redirect --hsts \
        -m eien86@toolassisted.run --agree-tos -n

echo
echo "site: HTTP $(curl -s -o /dev/null -w '%{http_code}' https://toolassisted.run/)"
curl -s https://toolassisted.run/ | grep -q betabar && echo "beta banner present"
echo "same-origin API: HTTP $(curl -s -o /dev/null -w '%{http_code}' https://toolassisted.run/archivist/api/me)"
