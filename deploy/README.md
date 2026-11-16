# Putting Tick and Book on the server

Two images, two containers (16/11/2026). Each image holds its frontend and its backend,
kept apart in separate folders:

| Image (file) | Container | Port | Inside | Address |
|---|---|---|---|---|
| `tickbook/airline-site` (`airline-site.tar`) | `airline_site` | 5176 | `/app/frontend` customer website, `/app/backend` its API (`APP_MODE=public`) | https://tickandbook.com |
| `tickbook/airline-admin` (`airline-admin.tar`) | `airline_admin` | 5177 | `/app/frontend` admin site, `/app/backend` admin API (`APP_MODE=admin`) | https://admin.tickandbook.com |

In each container nginx (port 80) serves `/app/frontend` and passes `/api/` and `/uploads/` to
the API, which listens only inside the container. Both use the same `backend/.env` and the main
MySQL database on the server, so every admin change reaches the website at once. Staff accounts
are in their own database (`ADMIN_MYSQL_DATABASE`, created on first use). Built from the one
`Dockerfile` at the top of the repo (`--build-arg SITE=public` or `SITE=admin`).

Two ways to deploy:

* **A. Image files from the laptop** (steps 0-5 below) - build on this PC, copy two files, load them on the server.
* **B. GitHub Actions** - pushing `main` builds the images, deploys to 172.16.7.41 and runs the
  smoke checks (`.github/workflows/ci-cd.yml`). Update the `AIRLINE_BACKEND_ENV` secret first (step 3).

---

## 0. Docker Desktop on the laptop (once)

Docker Desktop needs WSL (Windows Subsystem for Linux). Without it, it stops with
"wsl is not installed".

1. PowerShell **as Administrator**: `wsl --install --no-distribution`
2. **Restart Windows.**
3. Open Docker Desktop and wait for **Engine running**.

## 1. Build the two image files (laptop)

```
powershell -ExecutionPolicy Bypass -File deploy\build-images.ps1
```

It builds from the committed code (uncommitted changes are left out and listed) and makes
`deploy\out\tickandbook-<date-time>-<commit>\` with `airline-site.tar`, `airline-admin.tar`,
`docker-compose.yml`, `load-on-server.sh`, `SHA256.txt` and `BUILT-FROM.txt`. The folder is not
committed (`.gitignore`). `backend\.env` is **not** in it: it holds passwords.

### Running the images on this laptop

Use `docker compose` in the project folder - **not** the Run (play) button in Docker Desktop.
The Run button starts the image without `backend/.env` and without
`MYSQL_HOST=host.docker.internal`, so the API looks for MySQL inside the container itself and
stops with *"Can't connect to MySQL server on 'localhost:3306'"*.

```
docker compose up -d --no-build        # start both (website http://localhost:5176, admin http://localhost:5177)
docker compose ps                      # both should say (healthy)
docker compose logs -f airline_site    # watch the website's log (Ctrl+C to stop watching)
docker compose down                    # stop both
```

XAMPP MySQL must be running, and the dev servers (start_all.bat) must be stopped first: they use
the same ports 5176/5177.

## 2. Server requirements (once)

* Docker Engine with **Docker Compose 2.30 or newer** (`docker compose version`); the compose
  file reads `backend/.env` with `format: raw`, so a `$` inside a password is kept exactly.
* MySQL on the server, reachable from Docker containers at `host.docker.internal`
  (MySQL `bind-address` must not be only `127.0.0.1`, and the MySQL user must be allowed from
  the Docker network, e.g. `'user'@'172.%'`). The user also needs rights to create the
  admin database (`tickandbook_admin`), or create it by hand.
* Ports 80/443 free for the HTTPS front door (step 4). The server's other systems use 5173/8000.

## 3. `backend/.env` on the server

Start from `backend/.env.example`. Must be set (the admin container refuses to start otherwise):

* `JWT_SECRET_KEY` - 32+ random characters
* `ADMIN_JWT_SECRET_KEY` - 32+ random characters, **different** from `JWT_SECRET_KEY`
* `ADMIN_MYSQL_DATABASE=tickandbook_admin`
* `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE` (`MYSQL_HOST` is set by docker-compose)
* Travelport: `TP_AUTH_URL`, `TP_API_BASE` (live), `TP_USERNAME`, `TP_PASSWORD`, `TP_CLIENT_ID`,
  `TP_CLIENT_SECRET`, `TP_PCC`, `TP_ACCESS_GROUP`
* Card gateway: `PAYMENT_GATEWAY`, `CYBERSOURCE_ENV=live` **together with** live Travelport -
  the backend refuses to start if one is live and the other test.
* `CORS_ALLOWED_ORIGINS=https://tickandbook.com`
* `FRONTEND_BASE_URL=https://tickandbook.com`, `OAUTH_REDIRECT_BASE=https://tickandbook.com`
* `GOOGLE_CLIENT_ID/SECRET`, `MICROSOFT_CLIENT_ID/SECRET`, `MICROSOFT_TENANT=consumers`
* `TRUST_PROXY_HEADERS=true` (docker-compose sets it as well)
* Email (`SMTP_*`) and SMS when the gateways are ready; `ERP_*` for the production ERP.

Write values **without a space after `=`** (Docker keeps that space; the hotel card lines in the
laptop's `.env` have one - hotels are off, but fix it before turning hotels on).

For GitHub Actions (way B), the whole file goes into the repository secret `AIRLINE_BACKEND_ENV`.

## 4. Load and start (server)

Copy the folder from step 1 to the server (WinSCP / `scp`), put the server's `.env` in it as
`backend/.env`, and in that folder run:

```
bash load-on-server.sh
```

(or, from the repo folder: `bash load-on-server.sh /path/to/the/copied/folder`). It checks both
files are complete, checks the required `.env` settings (without printing them), loads the two
images, starts `airline_site` (5176) and `airline_admin` (5177), waits until both are healthy and
checks the sites and their APIs. Uploaded images (tour packages) are kept in the Docker volume
`airline_uploads`, shared by both sites and kept across redeploys.

The same by hand, if you prefer:

```
docker load -i airline-site.tar
docker load -i airline-admin.tar
DOCKERHUB_USERNAME=tickbook docker compose up -d --no-build --pull never
docker compose ps
```

## 5. Domains and HTTPS (server, once)

1. DNS: `A` records for `tickandbook.com`, `www.tickandbook.com` and `admin.tickandbook.com`
   pointing at the server's public IP.
2. HTTPS front door: `deploy/host-nginx/tickandbook.conf` (install steps at the top of the file;
   `certbot` adds the certificates). It sends the real visitor address on, which the login and
   booking limits need.
3. When HTTPS works, start the containers with `FRONTEND_BIND=127.0.0.1` so ports 5176/5177
   are no longer open to the network: `FRONTEND_BIND=127.0.0.1 docker compose up -d --no-build`.
4. Google and Microsoft sign-in: the redirect URIs
   `https://tickandbook.com/api/customers/oauth/google/callback` and
   `https://tickandbook.com/api/customers/oauth/microsoft/callback` must be registered.

## 6. Smoke checks (read-only)

```
bash deploy/run-smoke.sh                                         # on the server
SITE_URL=https://tickandbook.com ADMIN_URL=https://admin.tickandbook.com bash deploy/run-smoke.sh
powershell -ExecutionPolicy Bypass -File deploy\run-smoke.ps1    # on the laptop
```

Playwright checks in `e2e/` (run in Microsoft's Playwright Docker image, nothing to install).
They check that the pages open, that the customer site has no admin pages or staff API, that the
admin site has no search/booking/payment API, the security headers, and that staff routes need a
sign-in. **They never choose a flight, book, pay, issue a ticket or sign in.** Real bookings are
tested by a person, by hand, using the go-live switches (search / PNR / tickets).

Optional: `SMOKE_SEARCH=1 bash deploy/run-smoke.sh` (laptop: `run-smoke.ps1 -Search`) adds one
live flight search with the form's default route and checks results come back. It never presses
"Select Flight". Off by default: on live every search counts toward Travelport's look-to-book ratio.

## After going live

* Make the GitHub repository private and change every secret that was ever committed.
* Go-live switches: back to all ON after the live test.
* Staff Users & Roles: give the right people the Admin role; the old `admins` / `super_admins`
  tables in the main database can be removed once the admin site works.
