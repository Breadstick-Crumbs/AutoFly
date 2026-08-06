# Private web dashboard

The dashboard is an optional administration interface for one self-hosted AutoFly instance. It
shows watches, recent fare observations, cycle history, and source health. It can create, update,
enable, or disable watches and start a manual check. It has no booking or purchase capability.

## Native use

Install the optional dependencies and listen on loopback:

```bash
python -m pip install ".[web]"
autofly web --config ./config.yaml
```

Open `http://127.0.0.1:8080`. Loopback mode does not require a password because only local
processes can connect. To reach it from another computer, keep it on loopback and use SSH:

```bash
ssh -L 8080:127.0.0.1:8080 user@your-server
```

Then open `http://127.0.0.1:8080` on your computer.

## Docker Compose

The setup service creates `autofly-config/config.yaml`. Add a random dashboard password of at
least 16 characters to `.env`, then start the scheduler and optional dashboard:

```bash
printf '\nAUTOFLY_WEB_PASSWORD=%s\n' "$(openssl rand -base64 32)" >> .env
docker compose --profile web up -d autofly web
```

Compose publishes the dashboard as `127.0.0.1:8080:8080`; it is not reachable directly from the
network. Use the SSH tunnel above. Sign in with username `autofly` and the configured password.

The scheduler mounts configuration read-only. The dashboard alone has write access to
`autofly-config/`, while both services share the SQLite volume. AutoFly uses the existing process
lock, so a manual check cannot overlap a scheduled cycle.

## Remote access and reverse proxies

`--allow-remote` is required for a non-loopback bind and `AUTOFLY_WEB_PASSWORD` must then contain
at least 16 characters. HTTP Basic credentials are only encoded, not encrypted. Never expose the
dashboard over plain HTTP. Prefer an SSH tunnel; otherwise terminate HTTPS in a carefully
configured reverse proxy, restrict its network reach, and pass the `Authorization` header.

AutoFly deliberately provides no public registration, password recovery, multi-user roles, or
internet-facing deployment preset. It also disables interactive API documentation.

## Configuration safety

Every dashboard edit validates the entire configuration before it is written. Saving uses an
atomic file replacement and retains the previous file as `config.yaml.bak`. The editor may
normalize YAML formatting and comments; keep important commentary in version control or separate
documentation. Environment-variable overrides still take precedence at runtime.

The interface never returns or edits bot tokens, webhook URLs, environment values, or browser
profiles. Mutation requests require dashboard-origin headers in addition to authentication. A
manual check can send real notifications; the interface asks for confirmation first.

Arbitrary valid three-letter source identifiers remain accepted. Airport suggestions are a
convenience list, not a claim that every metropolitan code is supported by Flight GOAT.
