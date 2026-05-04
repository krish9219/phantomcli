# Phantom plugin mirror — production deploy

This directory ships the artifacts to bring up
`phantom.aravindlabs.tech/plugins` behind TLS.

## Quick start (Docker)

```bash
# 1. build the image from the repo root
docker build -f deploy/mirror/Dockerfile -t phantom-mirror:1.0.0 .

# 2. run with persistent storage
docker run -d --name phantom-mirror \
    --restart=always \
    -p 127.0.0.1:8801:8801 \
    -v /srv/phantom-mirror:/data \
    phantom-mirror:1.0.0

# 3. publish the launch plugins
docker exec phantom-mirror python -m phantom.cli plugin publish \
    /app/phantom/plugins/builtin/clock --store /data
docker exec phantom-mirror python -m phantom.cli plugin publish \
    /app/phantom/plugins/builtin/weather --store /data
docker exec phantom-mirror python -m phantom.cli plugin publish \
    /app/phantom/plugins/builtin/code_review --store /data
docker exec phantom-mirror python -m phantom.cli plugin publish \
    /app/phantom/plugins/builtin/github_pr --store /data
docker exec phantom-mirror python -m phantom.cli plugin publish \
    /app/phantom/plugins/builtin/web_screenshot --store /data
docker exec phantom-mirror python -m phantom.cli plugin publish \
    /app/phantom/plugins/builtin/todo --store /data
docker exec phantom-mirror python -m phantom.cli plugin publish \
    /app/phantom/plugins/builtin/code_search --store /data
docker exec phantom-mirror python -m phantom.cli plugin publish \
    /app/phantom/plugins/builtin/gh_search --store /data

# 4. install Caddy and front it
sudo cp deploy/mirror/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy

# 5. verify
curl -s https://phantom.aravindlabs.tech/plugins/healthz | jq
curl -s https://phantom.aravindlabs.tech/plugins/index.json | jq '.plugins | length'
```

## Quick start (systemd, no Docker)

```bash
# 1. install phantom-cli
pip install --user phantom-cli

# 2. create the mirror user + data dir
sudo useradd --system --create-home --home /srv/phantom-mirror phantom-mirror
sudo chown -R phantom-mirror:phantom-mirror /srv/phantom-mirror

# 3. install the unit
sudo cp deploy/mirror/phantom-mirror.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now phantom-mirror

# 4. publish bundles (run as the mirror user)
sudo -u phantom-mirror phantom plugin publish \
    /path/to/phantom/plugins/builtin/clock --store /srv/phantom-mirror

# 5. front with Caddy (same as Docker path)
sudo cp deploy/mirror/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

## Health check

```bash
curl https://phantom.aravindlabs.tech/plugins/healthz
# {"ok": true, "plugins": 8}
```

## Signing your published plugins

```bash
# 1. generate a keypair (one-time)
phantom plugin keygen > ~/.phantom/mirror-signing.json
# This file contains the private key — keep it offline.

# 2. publish with signing
phantom plugin publish ./my-plugin \
    --store /srv/phantom-mirror \
    --signing-key-file ~/.phantom/mirror-signing.json

# 3. consumers verify
phantom plugin install my-plugin --require-signed
```

## Rotating the mirror

If you need to point Phantom clients at a different mirror:

```bash
phantom config set plugin_mirror_url https://other.example/plugins
# or per-call:
phantom plugin install foo --mirror https://other.example/plugins
```

## Operational notes

* **Logs**: Docker — `docker logs phantom-mirror -f`. systemd — `journalctl -u phantom-mirror -f`.
* **Backup**: `/srv/phantom-mirror/index.json` + `/srv/phantom-mirror/plugins/` is the entire state. tar it.
* **Read-only mirror**: the Caddyfile rejects POST/PUT/DELETE from the public side. Publishing is operator-only via `docker exec` / `sudo -u phantom-mirror`.
* **DDoS resilience**: bundle URLs are immutable + cacheable; CDN them if traffic warrants. The index.json is small (a few KB at launch); 60-second cache keeps origin load minimal.
