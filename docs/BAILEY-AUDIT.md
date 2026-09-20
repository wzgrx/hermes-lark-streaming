# Bailey sidecar comparison (2026-09-20)

Reference: [`baileyh8/hermes-feishu-streaming-card`](https://github.com/baileyh8/hermes-feishu-streaming-card).

## Adopted in 0.15.0–0.16.0

- privacy-preserving doctor, health counters and API latency metrics;
- exact chat binding, separate clients per bot and credential indirection;
- adaptive coalescing/backpressure with serialized flushes;
- 28 KB / 200-element card inspection and deterministic Markdown compaction;
- signed, expiring, domain-separated sidecar events with bounded replay fencing;
- old tool/reasoning history summaries that preserve errors and the recent full window;
- explicit offline/live smoke boundary, optional process-isolated control plane;
- four locale payloads, icon-plus-text status and compact mobile snapshots;
- SBOM, checksums, build provenance, manual trusted PyPI publishing and rollback docs;
- stable initial-message UUIDs persisted across restarts and explicit `delivered/not_sent/unknown`
  outcome handling;
- SDK constructor probing and an explicit active-interpreter repair command.

## Deliberately retained from this project

- Hermes stays the only approval/clarify resolver. The plugin does not create a second interaction
  state machine, so a button cannot resolve a request twice.
- Card rendering remains in-process by default. The sidecar is optional observability/event intake,
  avoiding a mandatory service for single-bot installations.
- Native Hermes profile multiplexing remains the first isolation boundary; multi-bot routing is an
  additional exact chat mapping within one profile.

## Follow upstream

Hermes now publishes observer-only stream hooks, and 0.16.0 registers them for diagnostics.
They cannot claim delivery or suppress the platform sender, so AST hooks remain the reversible
compatibility path until Hermes publishes an owner-capable renderer API. Bailey's persistent
sidecar heartbeat/orphan recovery remains most useful for multi-process deployments; this project's
default in-process mode instead persists only delivery idempotency state and keeps service count low.
