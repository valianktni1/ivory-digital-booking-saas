# TrueNAS deployment — Phase One

Use a new private GitHub repository for this project. Do not place these files in the Weddings By Mark repository.

## 1. Put the repository in the Dockge stack directory

The checkout/compose directory should be:

```text
/mnt/apps/dockge/data/tenantsbookingsystem2026
```

The persistent datasets already chosen are:

```text
/mnt/tenant-weddingbookingsystem2026
/mnt/photographers_data/tenant-data-weddingbookingsystem2026
```

## 2. Create the private environment file

Run from the repository directory:

```bash
cd /mnt/apps/dockge/data/tenantsbookingsystem2026

cp .env.example .env

python3 scripts/generate-secrets.py
```

Copy the three generated values into `.env`. Also set a long unique `PLATFORM_ADMIN_PASSWORD`. Do not commit `.env` to GitHub.

## 3. Build and start

```bash
cd /mnt/apps/dockge/data/tenantsbookingsystem2026

sudo docker compose -f compose.yaml config

sudo docker compose -f compose.yaml build --no-cache backend worker manager studio client

sudo docker compose -f compose.yaml up -d

sudo docker compose -f compose.yaml ps
```

All seven services should be healthy/running. The one-off permissions service should show `Exited (0)`.

## 4. Check locally before opening Manager

```bash
curl -fsS http://127.0.0.1:30061/api/health

curl -I http://127.0.0.1:30060

curl -I http://127.0.0.1:30062/test-business
```

The health request should report `status: ok`.

## 5. First private Manager login

Open `https://manager.ivorydigital.uk` and use the email/password from `.env`. The Manager will require you to scan a QR code with an authenticator app. Save the eight recovery codes somewhere private before continuing.

Create only a fictional test photographer first. The Manager provides a single-use Studio setup link. Automatic messages remain paused and Phase One cannot contact couples.

## Back up the new SaaS database

This is a separate backup from Weddings By Mark:

```bash
SAAS_BACKUP="/mnt/tenant-weddingbookingsystem2026/ivory-saas-before-update-$(date +%Y%m%d-%H%M%S).dump"

sudo sh -c "docker exec tenantsbookingsystem2026-db pg_dump -U ivory_booking -d ivory_booking -Fc > '$SAAS_BACKUP'"

sudo ls -lh "$SAAS_BACKUP"
```

## Roll back safely

Keep the previous Git commit and database dump. If a deployment check fails, stop and inspect logs before changing data:

```bash
sudo docker compose -f /mnt/apps/dockge/data/tenantsbookingsystem2026/compose.yaml logs --tail=200 backend worker postgres redis
```
