# Putting Tick and Book on the server

Two images, four containers (16/11/2026). Each image holds its frontend and its backend, kept
apart in separate folders (`/app/frontend`, `/app/backend`), and runs as **two containers**:
one for the website, one for the API. Docker Desktop shows all four together under
**tickandbook** (Containers tab); each one can be started and stopped on its own.

| Image (file) | Container | What it runs | Port | Address |
|---|---|---|---|---|
| `tickbook/airline-site` (`airline-site.tar`) | `airline_site_frontend` | customer website (nginx, `/app/frontend`) | 5176 | https://tickandbook.com |
| | `airline_site_backend` | its API (`/app/backend`, `APP_MODE=public`) | - | - |
| `tickbook/airline-admin` (`airline-admin.tar`) | `airline_admin_frontend` | admin site (nginx, `/app/frontend`) | 5177 | https://admin.tickandbook.com |
| | `airline_admin_backend` | admin API (`/app/backend`, `APP_MODE=admin`) | - | - |

Each frontend passes `/api/` and `/uploads/` to its own backend. The backends are not published
(no port): they are reached only through their site. While a backend is stopped, its site still
opens and `/api/` answers 503 "Service temporarily unavailable". Both backends use the same
`backend/.env` and the main MySQL database on the server, so every admin change reaches the
website at once; the frontends get no settings at all. Staff accounts are in their own database
(`ADMIN_MYSQL_DATABASE`, created on first use). Built from the one `Dockerfile` at the top of the
repo (`--build-arg SITE=public` or `SITE=admin`); `ROLE=frontend` / `ROLE=backend` (set in
`docker-compose.yml`) chooses what a container runs.

To see the files: Docker Desktop -> Containers -> click a container -> **Files** tab ->
`/app/frontend` (website) and `/app/backend` (API). Or
`docker exec airline_site_backend ls /app/backend`.

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
stops. Since 16/11 the image then says so plainly in its log ("started WITHOUT its settings")
instead of a MySQL error.

Easiest: double-click **`deploy\local-test.bat`** (starts all four, the sites on **8176** / **8177**,
waits until healthy, opens both sites; the normal local system from start_all.bat keeps
5176 / 5177, so both can run) and **`deploy\local-stop.bat`** to stop. The same by hand:

```
docker compose up -d --no-build                 # start all four (website http://localhost:5176, admin http://localhost:5177)
docker compose ps                               # all four should say (healthy)
docker compose stop airline_site_backend        # stop one container (the website then answers 503 on /api/)
docker compose start airline_site_backend       # start it again
docker compose logs -f airline_site_backend     # watch one container's log (Ctrl+C to stop watching)
docker compose down                             # stop and remove all four
```

In Docker Desktop: Containers -> **tickandbook** -> the Start / Stop buttons on each row.

XAMPP MySQL must be running. By hand, the default ports are 5176/5177 (as on the server); to run
next to the dev servers use `SITE_PORT=8176 ADMIN_PORT=8177` like local-test.bat.

## 2. Server requirements (once)

* Docker Engine with **Docker Compose 2.30 or newer** (`docker compose version`); the compose
  file reads `backend/.env` with `format: raw`, so a `$` inside a password is kept exactly.
* MySQL on the server, reachable from Docker containers at `host.docker.internal`
  (MySQL `bind-address` must not be only `127.0.0.1`, and the MySQL user must be allowed from
  the Docker network, e.g. `'user'@'172.%'`). The user also needs rights to create the
  admin database (`tickandbook_admin`), or create it by hand.
* Ports 80/443 free for the HTTPS front door (step 4). The server's other systems use 5173/8000.

## 3. `backend/.env` on the server

Start from `backend/.env.example`. Must be set (the admin backend refuses to start otherwise):

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
images, removes containers left from an earlier layout, starts the four containers (website
5176, admin site 5177), waits until all four are healthy and checks the sites and their APIs. Uploaded images (tour packages) are kept in the Docker volume
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

Playwright tests in `e2e/tests/` (run in Microsoft's Playwright Docker image, nothing to install),
758 in six files, all in one group so the Playwright interface shows every one at once:

* **01-website** (8) - pages open, no admin pages or staff API, security headers.
* **02-website-sign-in** (12) - customer APIs need a token, forged / changed / expired tokens are
  refused, wrong passwords get the same message for every email, sign-up refuses bad details,
  Google and Microsoft sign-in start correctly, a made-up sign-in return gives no token, other
  websites can't call the API, and the test customer signs in (browser and API).
* **03-admin-site** (5) - staff sign-in page, no customer pages, no search/booking/payment API on the admin site.
* **04-admin-sign-in-and-roles** (12) - staff and analytics APIs need a token, forged tokens are
  refused, the four roles (Admin, Staff, Analytics, Staff + Analytics) each reach exactly what
  they should, disabled accounts can't sign in, customer and staff tokens only work on their own
  site, and Admin signs in and out in the browser.
* **05-cmb-routes** (500) - the CMB route test: the 52 destinations / 448 flights (361 GDS, 87 NDC)
  of the 7 Nov test, searched on the website for 27 Nov 2026 (Travelport, one-way, 1 adult). One
  test per flight: is that airline offered with that content, and what the fare includes (bags,
  carry-on, seat choice, meals, change/cancel fees). Only with `-Routes` (52 searches).
* **06-api** (221) - every backend API, one test each, from `e2e/data/api-catalog.json`
  (`py e2e\tools\api_catalog.py` rebuilds it from the local backend's API list; a last test fails
  when an API in `backend/main.py` has no test). Each test attaches the request and the reply
  (passwords, tokens and card fields masked). Reads (GET) are called without sign-in (protected
  ones must refuse with 401) and signed in with the temporary test Admin / customer. APIs that
  change something are only checked to refuse without sign-in; public logins and searches only
  with invalid input (422); the 23 public booking, payment, ticketing and cancel steps are never
  called (shown as skipped). Each API is also checked to be absent (404) on the other site. A
  test fails on a crash (5xx without a handled message), a wrong refusal, or personal details in
  a public reply; switched-off modules (503) and handled supplier errors pass with a note.

**No test ever chooses a flight, books, pays or issues a ticket.** Real bookings are tested by a
person, by hand, using the go-live switches (search / PNR / tickets). Seat maps and paid extras
need a started booking, so the route test reports what each fare includes instead.

On the laptop:

```
powershell -ExecutionPolicy Bypass -File deploy\run-smoke.ps1 -Accounts                    # smoke + sign-in + roles
powershell -ExecutionPolicy Bypass -File deploy\run-smoke.ps1 -Routes -Tests cmb-routes    # CMB route test only
py e2e\tools\route_report.py <run-id>                                                      # Excel report of a route run
deploy\playwright-ui.bat                                                                   # the interface with every test
```

`-Accounts` makes temporary test accounts in the LOCAL database (`e2e/tools/auth_accounts.py`,
names starting `e2e_`) and deletes them after the run; the role tests are skipped without them
(server, CI). The HTML report is `e2e\playwright-report\index.html`.

Optional: `SMOKE_SEARCH=1 bash deploy/run-smoke.sh` (laptop: `run-smoke.ps1 -Search`) adds one
live flight search with the form's default route and checks results come back. It never presses
"Select Flight". Off by default: on live every search counts toward Travelport's look-to-book ratio.

## After going live

* Make the GitHub repository private and change every secret that was ever committed.
* Go-live switches: back to all ON after the live test.
* Staff Users & Roles: give the right people the Admin role; the old `admins` / `super_admins`
  tables in the main database can be removed once the admin site works.
