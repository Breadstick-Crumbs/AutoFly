# Self-hosting AutoFly

AutoFly needs outbound HTTPS, Python 3.11+ (3.12 recommended), and Flight GOAT. It needs no GPU, inbound port, firewall change, or permanent web process.

## Ubuntu/Debian native install

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv nodejs npm git
sudo useradd --system --home /var/lib/autofly --create-home --shell /usr/sbin/nologin autofly
sudo install -d -o root -g root -m 0755 /opt/autofly /etc/autofly
sudo install -d -o autofly -g autofly -m 0700 /var/lib/autofly
sudo git clone https://github.com/Breadstick-Crumbs/AutoFly.git /opt/autofly
sudo python3.12 -m venv /opt/autofly/.venv
sudo /opt/autofly/.venv/bin/pip install /opt/autofly
sudo -u autofly npx -y @mvanhorn/printing-press-library@0.1.19 install flight-goat --cli-only
sudo cp /opt/autofly/config.example.yaml /etc/autofly/config.yaml
sudo cp /opt/autofly/.env.example /etc/autofly/autofly.env
sudo chown root:autofly /etc/autofly/config.yaml /etc/autofly/autofly.env
sudo chmod 0640 /etc/autofly/config.yaml
sudo chmod 0600 /etc/autofly/autofly.env
```

The npm installer requires Node 20+. If the distribution Node is older, install a maintained Node 20/22 package first. Ensure `flight-goat-pp-cli` is on the service user's `PATH`, or set its absolute path in YAML. Edit configuration paths to `/var/lib/autofly/autofly.db` and `/var/lib/autofly/autofly.lock`, add real secrets, then:

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

## Docker Compose

The image builds Flight GOAT from the exact tested source commit. No port is published.

```bash
cp config.example.yaml config.yaml
cp .env.example .env
# Edit both files and change database/lock paths to /var/lib/autofly/...
docker compose build
docker compose run --rm autofly doctor
docker compose run --rm autofly check --all --dry-run
docker compose run --rm autofly check --all
```

Schedule the last command from the host's systemd timer or cron. The named volume preserves SQLite history. Back it up by stopping checks and copying/exporting the volume; never copy a database while another cycle is writing.

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

