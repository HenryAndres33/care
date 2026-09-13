# Deploying CARE Suriname to a server

One small VPS (4 GB RAM, 2 vCPU, e.g. Hetzner CPX21 in Ashburn), Docker, and
two DNS records. Everything below is run on the server as a normal user in the
`docker` group.

## What runs

| Container       | Role                                            | Reachable from |
| --------------- | ----------------------------------------------- | -------------- |
| `caddy`         | HTTPS, certificates, routing                     | internet 80/443 |
| `frontend`      | React bundle (nginx)                             | caddy only |
| `backend`       | Django API (gunicorn)                            | caddy only |
| `celery-worker` | background jobs (PDF rendering, etc.)            | — |
| `celery-beat`   | runs migrations at start, then the scheduler     | — |
| `db`            | PostgreSQL 17 — the patient records              | internal only |
| `redis`         | queue / cache                                    | internal only |
| `minio`         | file store — PDFs and uploads                    | caddy only |

Routing: `https://DOMAIN/api/*` and `/admin/*` go to Django, everything else
to the React bundle, `https://files.DOMAIN` to MinIO (presigned PDF links).

## First deployment

1. **DNS** — point both `DOMAIN` and `files.DOMAIN` (A records) at the server
   IP. Caddy cannot get certificates until these resolve.

2. **Code** — clone both repositories side by side:

       git clone git@github.com:HenryAndres33/care.git     care
       git clone git@github.com:HenryAndres33/care_fe_private.git  care_fe
       cd care/deploy

3. **Secrets** — generate the settings file once:

       bash make-env.sh DOMAIN

   Check `CLINICAL_WORKFLOW_MUTATIONS_ENABLED_FACILITIES` in `.env` matches
   the facility UUID(s) that will exist after the data restore (step 5).

4. **Build and start** (first build ≈ 15 min):

       docker compose build
       docker compose up -d
       docker compose ps          # all "healthy" / "running"

5. **Restore data from the laptop** — a backup set from
   `scripts/care-suriname-backup.sh` has `database.dump` and `minio.tar.gz`:

       docker compose stop backend celery-worker celery-beat
       docker compose cp database.dump db:/tmp/
       docker compose exec db psql -U care -d postgres -c "select pg_terminate_backend(pid) from pg_stat_activity where datname='care' and pid<>pg_backend_pid()"
       docker compose exec db dropdb -U care care && docker compose exec db createdb -U care care
       docker compose exec db pg_restore -U care -d care --no-owner --role=care /tmp/database.dump
       # MinIO's image has no tar: unpack on the host, copy the two buckets in.
       mkdir -p /tmp/minio-restore && tar -xzf minio.tar.gz -C /tmp/minio-restore --strip-components=1 --exclude='minio/.minio.sys'
       docker compose stop minio
       docker compose cp /tmp/minio-restore/patient-bucket minio:/data/
       docker compose cp /tmp/minio-restore/facility-bucket minio:/data/
       docker compose start minio celery-beat celery-worker backend

   Verified 13 September 2026 against the laptop backup of that morning:
   36 patients, 15 users, 67 stored files; a finalized note PDF downloaded
   through `files.DOMAIN`.

6. **Check** — open `https://DOMAIN`, log in, open a patient, download a PDF.

## Updating

Commit on the laptop and push to GitHub. Then:

- **Backend changed** — on the server (or over ssh):

      bash ~/care-suriname/care/deploy/update.sh

  Pulls, rebuilds the backend image, restarts it; migrations run in
  `celery-beat` before the API starts. ~2–10 min.

- **Frontend changed** — from the laptop:

      bash care/deploy/ship-frontend.sh

  Compiles the bundle on the laptop (~2 min), packs it into the nginx image,
  sends it over ssh and restarts only `frontend`. The server never compiles
  the frontend: with 4 GB it cannot do so while also serving the clinic.

`update.sh` refuses nothing, but it also never builds the frontend; it tells
you when a ship is due.

## Backups on the server

Nightly `pg_dump` + MinIO archive into `./backups`, then a copy pulled to the
laptop — set up in step 2 of the hosting plan; not yet scripted here.
Also enable the provider's whole-disk snapshot (Hetzner: Backups add-on).

## Local test of this stack

See [local-test/README.md](local-test/README.md).

## What is deliberately absent

- No email/SMS transport (`USE_SMS=false`, no `EMAIL_*`); pilot decision D1.
- No Sentry, no CDN, no draft-recovery key file — same as the laptop.
- `DJANGO_SECURE_SSL_REDIRECT=false`: Caddy already forces HTTPS, and the
  container healthcheck talks plain HTTP to itself.
