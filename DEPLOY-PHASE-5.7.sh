#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
cd /mnt/apps/dockge/data/tenantsbookingsystem2026
if [ "$(id -u)" -ne 0 ]; then
  echo "Run: sudo bash DEPLOY-PHASE-5.7.sh"
  exit 1
fi
compose=(docker compose -f /mnt/apps/dockge/data/tenantsbookingsystem2026/compose.yaml)
release_marker=phase-five-seven-one-help
python3 - <<'PY'
from pathlib import Path
for name in ['app/main.py','studio/index.html','client/index.html','manager/index.html']:
    if 'phase-five-seven-one-help' not in Path(name).read_text():
        raise SystemExit('The complete Phase 5.7 source is not present. Check your GitHub push and private git pull first.')
PY
release_stamp=$(date +%Y%m%d-%H%M%S)
release_backup="/mnt/apps/dockge/backups/tenantsbookingsystem2026/phase57-$release_stamp"
mkdir -p "$release_backup"
chmod 700 "$release_backup"
trap 'echo "Update stopped. Backup and rollback files: $release_backup. Check the error above before continuing."' ERR

echo "Saving the database before any running service is changed..."
docker exec tenantsbookingsystem2026-db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$release_backup/database.dump"
test -s "$release_backup/database.dump"
docker exec -i tenantsbookingsystem2026-db pg_restore --list < "$release_backup/database.dump" > "$release_backup/database-contents.txt"
test -s "$release_backup/database-contents.txt"
sha256sum "$release_backup/database.dump" > "$release_backup/database.dump.sha256"
backup_bytes=$(stat -c %s "$release_backup/database.dump")
backup_created=$(date -u +%Y-%m-%dT%H:%M:%SZ)
git rev-parse HEAD > "$release_backup/source-commit.txt"

# Keep the actual running images, even if the source checkout has already moved forward.
# Only services that already exist are included. Database and Redis are never replaced.
printf 'services:\n' > "$release_backup/rollback-images.yaml"
for service in backend worker studio client manager; do
  if image_id=$(docker inspect --format '{{.Image}}' "tenantsbookingsystem2026-$service" 2>/dev/null); then
    rollback_tag="ivory-phase57-rollback-$service:$release_stamp"
    docker tag "$image_id" "$rollback_tag"
    printf '  %s:\n    image: %s\n' "$service" "$rollback_tag" >> "$release_backup/rollback-images.yaml"
  fi
done
cat > "$release_backup/ROLLBACK.sh" <<'ROLLBACK'
#!/usr/bin/env bash
set -Eeuo pipefail
rollback_dir=$(cd -- "$(dirname -- "$0")" && pwd)
cd /mnt/apps/dockge/data/tenantsbookingsystem2026
docker compose -f compose.yaml -f "$rollback_dir/rollback-images.yaml" up -d --no-build --no-deps --force-recreate backend worker studio client manager
docker compose -f compose.yaml ps
printf '\nPrevious application images restored. Database records have not been rolled back.\n'
ROLLBACK
chmod 700 "$release_backup/ROLLBACK.sh"
printf 'Database backup and rollback images saved at: %s\n' "$release_backup"

# Uses the existing private GitHub authentication. No tokens are read or printed here.
echo "Building all five application services..."
"${compose[@]}" build --no-cache backend worker studio client manager

wait_healthy() {
  local service="$1" health=""
  for attempt in $(seq 1 60); do
    health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "tenantsbookingsystem2026-$service" 2>/dev/null || true)
    if [ "$health" = healthy ]; then
      echo "$service is healthy"
      return 0
    fi
    if [ "$health" = exited ] || [ "$health" = dead ]; then break; fi
    sleep 2
  done
  echo "$service did not become healthy. Review: docker logs --since 10m tenantsbookingsystem2026-$service"
  return 1
}

# Stop old background processing only during the backend change.
"${compose[@]}" stop worker
"${compose[@]}" up -d --no-deps --force-recreate backend
wait_healthy backend
"${compose[@]}" up -d --no-deps --force-recreate studio client manager worker
for service in studio client manager worker; do wait_healthy "$service"; done
for port in 30060 30061 30062; do
  response=$(curl -fsS "http://127.0.0.1:$port/api/health")
  case "$response" in *"$release_marker"*) echo "Port $port: Phase 5.7 verified";; *) echo "Wrong release on port $port"; exit 1;; esac
done
docker exec -e "IVORY_BACKUP_BYTES=$backup_bytes" -e "IVORY_BACKUP_CREATED=$backup_created" tenantsbookingsystem2026-backend python -c 'import json,os; from app.config import get_settings; p=get_settings().platform_storage_root/"last-deployment-backup.json"; p.write_text(json.dumps({"created_at":os.environ["IVORY_BACKUP_CREATED"],"size_bytes":int(os.environ["IVORY_BACKUP_BYTES"]),"archive_checked":True}))'
"${compose[@]}" ps
printf '\nPhase 5.7 is running. Database archive checked; this is not a full restore test.\n'
printf 'Rollback command if needed: sudo bash %s/ROLLBACK.sh\n' "$release_backup"
