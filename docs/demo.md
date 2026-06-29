# Recording the Argos demo

The 90-second story: a healthy multi-agent run looks calm and green, then a
misbehaving run makes the dashboard light up **red** — a runaway loop, repeated
tool failures, and a cost spike — all caught and alerted on automatically.

Everything below assumes you're at the repo root with the Python env that has the
SDK + backend installed (`pip install -e "sdk[dev,kafka]" -e "backend[dev]"`).

## 1. Bring up the stack

```bash
docker compose up -d
```

This starts Kafka, ClickHouse, Prometheus, and **Grafana**. Grafana comes up
already wired — datasources and the dashboard are provisioned from
`deploy/grafana/`, so there's nothing to click.

> First run only: Grafana downloads the ClickHouse datasource plugin, which needs
> internet that one time. Give it ~20s, then it's cached in the `grafana-data`
> volume.

Open **http://localhost:3000** — you land straight on *“Argos — Multi-Agent Trace
Overview”* (anonymous viewer access, no login).

## 2. Start the detector in watch mode

```bash
python -m backend.detection --watch
```

This serves `/metrics` on `:9108` (Prometheus scrapes it via
`host.docker.internal`) and evaluates any **new** trace that appears. It ignores
traces that already existed when it started, so the dashboard begins **calm**:

- *Critical findings* = **0** (green)
- *Last run cost*, the *loops* / *tool failures* gauges = **0** (green)

Leave this running. That's your calm/green beauty shot.

## 3. Inject trouble — the panels go red

In another terminal:

```bash
python examples/emit_bad_trace.py
```

This writes one deliberately broken trace into ClickHouse: the search agent loops
on `web.search` 10×, a flaky tool fails 6×, and the run costs $2.20. Within a few
seconds the watcher scores it and the dashboard flips:

- **Critical findings** stat turns **red** (≥1)
- **Last run cost** stat turns **red** (> $1.00)
- **Loops** and **Tool failures** gauges spike into the red zone
- **Findings by rule** timeseries shows bars climb for each rule
- **Recent runs** table shows the new `bad-…` row with red `errors` / `cost_usd`
  cells

In the watch terminal you'll also see the printed findings, e.g.
`[CRITICAL] runaway_loop: search repeated 'web.search' (tool_call) 10x`.

## 4. (Optional) Show the metric in Prometheus

Open **http://localhost:9090**, query `argos_findings_total`, Execute — you'll see
the `{rule="runaway_loop", severity="critical"}` series with a count. That's the
raw signal Grafana is drawing, and the thing that would page someone in prod.

## Reset between takes

The `--watch` process only reacts to traces newer than when it started, so for a
clean second take just **restart it** (Ctrl-C, run again). To wipe all data and
start completely fresh: `docker compose down -v` then back to step 1.

## Troubleshooting

- **Panels say “No data” for the Prometheus stats** — the detector isn't running
  or Prometheus can't reach it. Check `http://localhost:9090/targets`: the
  `argos-detector` target should be **up**. If it's down, confirm `--watch` is
  running and that `host.docker.internal:9108` is reachable (Phase 4 note).
- **ClickHouse tables empty / error** — the plugin didn't finish installing on
  first boot (no internet), or no spans exist yet. Re-`up` Grafana once online,
  and make sure you've emitted at least one trace.
