# Verifying Phase 2 — span survives app → Kafka → ClickHouse → restart

This proves the Phase 2 "done" condition: a span flows from the app through
Kafka into ClickHouse, and **survives a ClickHouse restart**.

> All commands are PowerShell, run from the repo root (`D:\Argos`). You need
> Docker Desktop running.

## 1. Bring up the stack

```powershell
docker compose up -d
docker compose ps        # wait until both services show "healthy"
```

This starts Kafka (KRaft, port 29092 for your host) and ClickHouse (port 8123).
On first boot ClickHouse auto-runs `backend/storage/schema.sql`, creating the
`argos.spans` table.

## 2. Install dependencies (once)

```powershell
pip install -e "sdk[kafka]"
pip install -r backend/requirements.txt
```

## 3. Start the consumer (terminal A)

```powershell
python -m backend.ingest.consumer
```

You should see:
`[argos-ingest] consuming 'argos.spans' @ localhost:29092 -> ClickHouse argos.spans`

Leave it running.

## 4. Emit a span to Kafka (terminal B)

```powershell
$env:ARGOS_KAFKA_BOOTSTRAP = "localhost:29092"
python examples/research-assistant/run_demo.py
```

Terminal A should print `[argos-ingest] inserted 1 span(s)`.

## 5. Confirm it landed in ClickHouse

```powershell
docker compose exec clickhouse clickhouse-client --user argos --password argos --query "SELECT trace_id, agent_name, cost_usd, redacted FROM argos.spans"
```

You should see your span. Note `redacted = 1` and that the `attributes` column
holds the scrubbed JSON (no secrets).

> The `--user argos --password argos` match the defaults in `docker-compose.yml`
> and `get_client()`. Override all three (compose, client, here) by setting
> `CLICKHOUSE_USER` / `CLICKHOUSE_PASSWORD` in your shell before `up`.

## 6. The restart test

```powershell
docker compose restart clickhouse
# wait a few seconds for it to come back, then query again:
docker compose exec clickhouse clickhouse-client --user argos --password argos --query "SELECT count() FROM argos.spans"
```

The row is **still there** — because ClickHouse writes to the named volume
`clickhouse-data`, which `restart` (and even `down` without `-v`) preserves. ✅

## Cleanup

```powershell
docker compose down        # stops containers, KEEPS data volumes
docker compose down -v     # also DELETES the data volumes (full reset)
```

## Optional: run the automated integration test

```powershell
$env:ARGOS_INTEGRATION = "1"
pytest backend/tests/test_integration.py -v
```

## Troubleshooting

- **Consumer can't connect to Kafka:** make sure you used `localhost:29092`
  (the host listener), not `9092` (that's the in-Docker address). See the
  listener comments in `docker-compose.yml`.
- **`argos.spans` table missing:** the schema only auto-loads on a *fresh* data
  volume. If you changed the schema, run `docker compose down -v` then `up`.
- **`AUTHENTICATION_FAILED` (code 516):** the ClickHouse user/password are baked
  into the data volume on first boot and are NOT updated by later env changes.
  After changing credentials you must recreate the volume:
  `docker compose down -v` then `docker compose up -d`.
