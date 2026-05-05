# Eve-NG Lab — Dedicated NetBox

Isolated NetBox instance for the Eve-NG_Agent project. Includes the
`netbox-bgp` plugin pre-installed.

- **URL:** http://localhost:8002
- **Default admin:** `admin` / `admin`
- **API token:** `eve1ng10001000100010001000100010001000abcd`

## Bring up

```bash
cd netbox
docker compose up -d --build
# wait ~90s for migrations on first start; check with:
docker compose logs -f netbox
```

## Tear down

```bash
docker compose down            # stop containers, keep data volumes
docker compose down -v         # also wipe data (postgres, redis, media)
```

## Why a separate NetBox

The Eve-NG_Agent project deliberately runs its own NetBox so that:
- The `netbox-bgp` plugin can be enabled without affecting other projects
- Lab-side data doesn't co-mingle with NetworkOps_Agent's containerlab inventory
- The schema can evolve independently

## Updating the plugin list

Edit `plugins.txt` to add/remove plugins, then:

```bash
docker compose up -d --build
```

The plugin must also be registered in `configuration/plugins.py` (`PLUGINS` list)
with any required `PLUGINS_CONFIG` entries.

## Secrets

The values in `env/*.env` and `configuration/configuration.py` are lab defaults.
Replace `SECRET_KEY`, DB password, Redis passwords, and the superuser API token
before exposing this instance beyond localhost.
