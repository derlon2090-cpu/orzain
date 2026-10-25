#!/bin/sh
set -eu

cert_dir="/etc/letsencrypt/live/${DOMAIN}"
fingerprint() {
    if [ -r "$cert_dir/fullchain.pem" ] && [ -r "$cert_dir/privkey.pem" ]; then
        sha256sum "$cert_dir/fullchain.pem" "$cert_dir/privkey.pem" 2>/dev/null | sha256sum | cut -d ' ' -f 1
    fi
}

last_fingerprint="$(fingerprint || true)"
nginx -g 'daemon off;' &
nginx_pid=$!
trap 'nginx -s quit 2>/dev/null || true; wait "$nginx_pid" 2>/dev/null || true; exit 0' TERM INT QUIT

while kill -0 "$nginx_pid" 2>/dev/null; do
    sleep 30 & wait $! || true
    current_fingerprint="$(fingerprint || true)"
    if [ -n "$current_fingerprint" ] && [ "$current_fingerprint" != "$last_fingerprint" ]; then
        if nginx -t; then
            nginx -s reload
            last_fingerprint="$current_fingerprint"
        fi
    fi
done
wait "$nginx_pid"
