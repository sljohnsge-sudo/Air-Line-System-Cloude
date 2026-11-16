# Putting Tick and Book on the server

Two sites from one code base (16/11/2026):

| Site | Address | Container (port) | API container |
|---|---|---|---|
| Customer website | https://tickandbook.com | `airline_frontend` (5176) | `airline_backend` (127.0.0.1:8005, `APP_MODE=public`) |
| Admin site (staff) | https://admin.tickandbook.com | `airline_admin_frontend` (5177) | `airline_admin_backend` (127.0.0.1:8006, `APP_MODE=admin`) |

Both APIs use the same image, the same `backend/.env` and the main MySQL database on the
server, so every admin change reaches the website at once. Staff accounts are in their own
database (`ADMIN_MYSQL_DATABASE`, created on first use).

Two ways to deploy:

* **A. Image file from the laptop** (steps 0-5 below) - build on this PC, copy one file, load it on the server.
* **B. GitHub Actions** - pushing `main` builds the images, deploys to 172.16.7.41 and runs the
  smoke checks (`.github/workflows/ci-cd.yml`). Update the `AIRLINE_BACKEND_ENV` secret first (step 3).

---

## 0. Docker Desktop on the laptop (once)

Docker Desktop needs WSL (Windows Subsystem for Linux). Without it, it stops with
"wsl is not installed".

1. PowerShell **as Administrator**: `wsl --install --no-distribution`
2. **Restart Windows.**
3. Open Docker Desktop and wait for **Engine running**.

## 1. Build the image file (laptop)

```
powershell -ExecutionPolicy Bypass -File deploy\build-images.ps1
```

It makes `deploy\out\tickandbook-<date-time>\` with `tickandbook-images.tar` (all three
images), `docker-compose.yml`, `load-on-server.sh` and `SHA256.txt`. The folder is not
committed (`.gitignore`). `backend\.env` is **not** in it: it holds passwords.

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

Copy the folder from step 1 to the server (WinSCP / `scp`). In the folder that has
`docker-compose.yml` and `backend/.env` (the repo folder):

```
bash load-on-server.sh /path/to/tickandbook-images.tar
```

It checks the file is complete, checks the required `.env` settings (without printing them),
loads the images, starts the four containers, waits until they are healthy and checks
5176 / 5177 / 8005 / 8006. Uploaded images (tour packages) are kept in the Docker volume
`airline_uploads`, shared by both sites and kept across redeploys.

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
sign-in. **They never search, choose a flight, book, pay, issue a ticket or sign in.** Real
bookings are tested by a person, by hand, using the go-live switches (search / PNR / tickets).

## After going live

* Make the GitHub repository private and change every secret that was ever committed.
* Go-live switches: back to all ON after the live test.
* Staff Users & Roles: give the right people the Admin role; the old `admins` / `super_admins`
  tables in the main database can be removed once the admin site works.
