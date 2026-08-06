# Self-hosting AutoFly

AutoFly needs outbound HTTPS. Its scheduler needs no GPU, inbound port, or firewall change. Docker
Compose is the shortest supported path; native systemd remains the strongest least-privilege
option for a shared server. The optional dashboard stays on loopback unless deliberately changed.

## Docker Compose

Docker 24+ with the Compose plugin is recommended. The image builds Flight GOAT from the exact
tested source commit and supports Linux AMD64 and ARM64. The scheduler publishes no port.

```bash
git clone https://github.com/Breadstick-Crumbs/AutoFly.git
cd AutoFly
docker compose run --rm setup
docker compose run --rm autofly doctor
docker compose run --rm autofly check --all --dry-run
docker compose up -d autofly
docker compose ps
docker compose logs -f autofly
```

Linux accounts whose UID or GID is not 1000 should pass their IDs to the setup container:

```bash
AUTOFLY_UID=$(id -u) AUTOFLY_GID=$(id -g) docker compose run --rm setup
```

The main container runs once immediately and then waits for `scheduler.interval_hours` plus a
random jitter. It restarts after a host reboot unless explicitly stopped. The named
`autofly-data` volume preserves SQLite history. Stop the service before copying the volume for a
backup.

Setup stores configuration at `autofly-config/config.yaml`. The scheduler mounts that directory
read-only. To enable the private dashboard, add a random value of at least 16 characters as
`AUTOFLY_WEB_PASSWORD` in `.env`, then run:

```bash
docker compose --profile web up -d autofly web
ssh -L 8080:127.0.0.1:8080 user@your-server
```

Open `http://127.0.0.1:8080` locally and sign in as `autofly`. Compose binds only to the server's
loopback interface. See [`dashboard.md`](dashboard.md) for the security model and reverse-proxy
requirements.

### Upgrading an existing Compose checkout from 0.1

The 0.1 Compose setup stored `config.yaml` in the repository root. Preserve it and copy it into the
directory used by 0.2 before starting the new containers:

```bash
mkdir -p autofly-config
cp -p config.yaml autofly-config/config.yaml
docker compose run --rm autofly doctor
```

Keep the original until the upgraded service and dashboard have both been verified. If the
container runs under a custom UID/GID, ensure that account owns `autofly-config/` so dashboard
edits can use atomic replacement.

## Ubuntu/Debian native install

For a personal account without root deployment, run `./scripts/install.sh`; it installs the pinned
Flight GOAT build and AutoFly under the current user. For a dedicated system account:

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv git curl tar coreutils
sudo useradd --system --home /var/lib/autofly --create-home --shell /usr/sbin/nologin autofly
sudo install -d -o root -g root -m 0755 /opt/autofly /etc/autofly
sudo install -d -o autofly -g autofly -m 0700 /var/lib/autofly
sudo git clone https://github.com/Breadstick-Crumbs/AutoFly.git /opt/autofly
sudo python3.12 -m venv /opt/autofly/.venv
sudo /opt/autofly/.venv/bin/pip install /opt/autofly
sudo -H -u autofly /opt/autofly/scripts/install-flight-goat.sh
sudo cp /opt/autofly/config.example.yaml /etc/autofly/config.yaml
sudo cp /opt/autofly/.env.example /etc/autofly/autofly.env
sudo chown root:autofly /etc/autofly/config.yaml /etc/autofly/autofly.env
sudo chmod 0640 /etc/autofly/config.yaml
sudo chmod 0640 /etc/autofly/autofly.env
```

The installer verifies the official Go archive checksum, installs it without `sudo`, and builds
Flight GOAT from the commit recorded in the script. Ensure `~autofly/.local/bin` is on the service
`PATH`, or set the absolute command path in YAML. Edit state paths to `/var/lib/autofly`, add real
secrets, then:

```bash
sudo -u autofly env AUTOFLY_CONFIG=/etc/autofly/config.yaml /opt/autofly/.venv/bin/autofly doctor
sudo -u autofly env AUTOFLY_CONFIG=/etc/autofly/config.yaml /opt/autofly/.venv/bin/autofly check --all --dry-run
```

## systemd timer

```bash
sudo cp /opt/autofly/deploy/systemd/autofly.service /etc/systemd/system/
sudo cp /opt/autofly/deploy/systemd/autofly.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now autofly.timer
systemctl list-timers autofly.timer
journalctl -u autofly.service
```

The timer is persistent, adds randomized delay, and uses journald. The service is a hardened non-root oneshot and AutoFly also holds its process lock.

## Cron

Install [`deploy/cron.example`](../deploy/cron.example) with `sudo crontab -u autofly -e`. Prefer systemd where available because missed timer runs are recovered by `Persistent=true`. Keep secret values out of the crontab.

## AMD64, ARM64, and NVIDIA GX10-class hosts

Python and Go/Flight GOAT support both common Linux architectures. Docker BuildKit builds the Go binary for the target platform; native installs need matching Python, Node, and Flight GOAT binaries. On ARM64 Ubuntu (including NVIDIA GX10 or similar systems), use ARM64 packages and run the same one-shot service. CUDA, GPU drivers, models, and accelerators are irrelevant and unused. Chromium availability matters only when the optional Playwright extra is enabled.

## Updating and rollback

Native update:

```bash
sudo systemctl stop autofly.timer
cd /opt/autofly
sudo git fetch --tags origin
sudo git checkout <reviewed-tag-or-commit>
sudo /opt/autofly/.venv/bin/pip install /opt/autofly
sudo -u autofly env AUTOFLY_CONFIG=/etc/autofly/config.yaml /opt/autofly/.venv/bin/autofly doctor
sudo systemctl start autofly.timer
```

Before upgrading, stop the timer and back up `/var/lib/autofly` plus `/etc/autofly`. Roll back by checking out the previous reviewed tag/commit, reinstalling it, restoring the matching database backup only if its schema is newer than the old app supports, running doctor, and restarting the timer. Never downgrade a database in place.

Docker update and rollback:

```bash
docker compose stop autofly
git fetch --tags origin
git checkout <reviewed-tag-or-commit>
docker compose build --pull
docker compose run --rm autofly doctor
docker compose up -d autofly
```

Roll back by repeating those steps with the previous reviewed tag. Back up
`autofly-config/config.yaml`, `.env`, and the `autofly-data` volume first. Never use an older
application against a database whose schema it does not support.
