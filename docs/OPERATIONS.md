# Operations and high-concurrency deployment

## Diagnostics

```bash
hermes-lark-streaming doctor
hermes-lark-streaming doctor --json
hermes-lark-streaming metrics --json
hermes-lark-streaming metrics --sidecar
hermes-lark-streaming smoke
hermes-lark-streaming lark-cli-smoke
hermes-lark-streaming repair-sdk  # only after doctor reports a broken SDK
```

CardKit errors are counted by operation and by a fixed, privacy-preserving
error-code bucket, for example `api.cardkit_stream_element.error_code.300309`
(stream already closed), `.300313` (element missing), `.300317` (sequence
conflict), or `.other`. Compare these with the corresponding `attempt` and
`success` counters before changing retry behavior. The metrics command reads
the last persisted gateway snapshot, so check `started_at` and `updated_at`
before treating it as the current process. `process_role` must be `gateway`;
the `--sidecar` selector reads a separate sidecar snapshot. Error counts include individual
retry attempts, not just failed cards.
If no readable persisted snapshot exists, the command reports
`metrics_unavailable` and exits nonzero; it does not present an empty CLI-process
snapshot as if it came from the Gateway.
Gateway and optional sidecar writers now publish different process snapshots:
`hermes-lark-streaming-metrics.json` and
`hermes-lark-streaming-metrics-sidecar.json`. Both use unique staging files
before atomic replacement. A legacy snapshot without `process_role` has
ambiguous ownership and is reported as `metrics_unavailable` until its process
writes a fresh snapshot; these files are not an aggregate.
The test suite uses a temporary metrics path and leaves the operator's live
snapshot untouched.
`api.element_not_found_recovered` counts only a successful stream or
partial-only batch update after a `300313` visibility retry. Partial-only
batch retries reuse the original sequence for at most two delays (200/400 ms);
batches that add elements go straight to the controller's state-reconciliation
path instead of replaying a potentially non-idempotent add on `300313`.
For server-transient retries of any batch, the SDK request carries a stable
CardKit idempotency UUID derived from card ID, sequence, and canonical actions.
This keeps retries of an already-applied `add_elements` batch in the same
operation; a repaired batch with changed actions gets a different UUID.
Streaming text, full-card update, and close-streaming requests likewise carry
operation-specific UUIDs, keeping their transient retries tied to the same
CardKit mutation. Card creation has no UUID field in the supported SDK, so
its ambiguous server outcome remains a distinct lifecycle case.

Repeated stream-element failures use a per-card 0.5–30 second exponential
retry delay instead of calling CardKit on every flush. Dirty text remains in
the local segment model; a later successful flush resets the delay, a new card
starts fresh, and terminal close/full-card update is independent of this
streaming delay. The `cardkit.stream.retry_deferred` and
`cardkit.stream.backoff_skipped` counters show this protection in action.

`smoke` is offline by default. A real CardKit create → stream → close → update check is explicit:

```bash
hermes-lark-streaming smoke --execute --chat-id oc_TEST_CHAT
```

For a narrower live check without a chat message, run
`hermes-lark-streaming smoke --execute --entity-only`. It creates one
unattached CardKit entity, intentionally streams to a missing element to
obtain `300313`, inserts that element, retries the *identical* stream
mutation/UUID, then closes the entity. The JSON output contains only the
error code and acceptance/close status; success requires both the retry and
close to succeed. This probes UUID reuse after a rejected update, not heavy
tool-turn delivery or all paths in upstream #98. It makes real Feishu API
calls and requires the active Hermes profile's Feishu credentials.

The optional official `lark-cli` is used only for operational preflight; it is not a runtime
dependency. `lark-cli-smoke --execute` runs read-only auth-status/schema commands.

## Adaptive backpressure and history compaction

```yaml
streaming:
  adaptive_backpressure:
    enabled: true
    min_ms: 100
    max_ms: 1500
  history_compaction:
    compact_after: 48
    keep_recent: 24
```

Rate limiting doubles the interval within the configured bound. Successful low-latency batches
gradually return it to the minimum. Terminal cards summarize old successful tool steps, retain
all old errors and keep the newest window at full fidelity.

## Multi-bot exact routing

```yaml
streaming:
  bots:
    default: primary
    chat_bindings:
      oc_EXACT_CHAT: support
    items:
      primary:
        app_id_env: FEISHU_PRIMARY_APP_ID
        app_secret_env: FEISHU_PRIMARY_APP_SECRET
      support:
        app_id_env: FEISHU_SUPPORT_APP_ID
        app_secret_env: FEISHU_SUPPORT_APP_SECRET
        base_url: https://open.feishu.cn
```

Each bot gets a separate SDK client/token cache. Bindings are exact; an unknown chat uses the
default bot. The configuration stores only environment-variable names.

## Optional sidecar

```bash
export HERMES_LARK_SIDECAR_SECRET='generate-a-random-secret'
hermes-lark-streaming sidecar --host 127.0.0.1 --port 8788
```

`GET /health` and `GET /metrics` expose no message content. `POST /events` requires a
domain-separated HMAC proof with timestamp and nonce; the replay cache is TTL- and size-bounded.
In-process card delivery remains the default and the sidecar is not required for normal use.



## Crash-safe delivery ledger

Initial CardKit attachment uses a logical-delivery fingerprint and a stable Feishu request UUID.
The local ledger distinguishes four states:

- `pending`: request prepared but no terminal evidence;
- `delivered`: Feishu returned the card message id; a restart resumes without sending again;
- `not_sent`: a structured non-transient Feishu response proved rejection, so a fresh attempt is safe;
- `unknown`: a timeout/transport failure may have committed remotely, so the answer is not duplicated.

The ledger is stored at `~/.hermes/state/hermes-lark-streaming-delivery.json`, atomically replaced,
mode `0600`, bounded to 1,024 rows. Terminal results expire after seven days; unresolved
`pending`/`unknown` records retain their request UUID beyond that window and take capacity
priority over terminal rows. At the unresolved-record limit, new sends stop before network I/O
until an operator resolves old outcomes, rather than discarding duplicate-prevention evidence.
Lark's message UUID deduplication window is one hour; retries stop after 55 minutes to leave
room for scheduling and network latency. An older recovered `pending` attempt is marked `unknown`,
and aged uncertain card/notice attempts are held for receipt inspection
instead of re-sent with an expired UUID. A verified `not_sent` retry starts a fresh attempt window.
Logical keys are SHA-256 fingerprints; message bodies, credentials, user ids and chat ids are not
stored. `doctor --json` reports counts only.
Gateway and cron synchronize the full read/modify/replace cycle through the adjacent
`hermes-lark-streaming-delivery.json.lock` file (also mode `0600`), preventing concurrent
processes from dropping each other's delivery evidence. Keep this lock file in place while
Hermes processes are running; the OS releases its lock when a process exits.
Malformed JSON, an unknown schema, or an unreadable entry now stops ledger mutation and
preserves the original file for inspection. Back up the file before any manual repair;
starting a fresh empty ledger may lose the evidence that prevents duplicate delivery.
When a live turn encounters this condition, the plugin holds its answer instead of
letting Hermes replay it as a second plaintext send. It attempts a short, stable-UUID
operator notice; the log retains the full diagnostic. A ledger error after a successful
final card update does not retry that update.

If an attach outcome remains `unknown`, CardKit entity updates continue and the plugin emits one
idempotent generic refresh notice immediately after the ambiguous attach. Completion rechecks
the durable notice receipt without sending a duplicate. This avoids making a user wait for a
long-running agent turn before learning that the card might not be visible. It does not resend
the answer as plaintext. An uncertain notice send is also retained in the ledger rather than
replayed after Lark's UUID deduplication window.

## Cron CardKit outcome ownership

The injected Cron path distinguishes a verified card message ID from a confirmed
server rejection and an ambiguous send outcome. A confirmed rejection allows
Hermes' native delivery path. A transport timeout, an unstructured exception
after the CardKit send starts, or a missing message ID yields an unverified
`delivery_outcome=unknown` receipt: the hook records the unverified target and
skips native plaintext replay. In particular, the 30-second Gateway-loop wait
may expire while its send coroutine is still running; falling through at that
point could deliver the same answer twice. Unknown outcomes are visible through
Cron delivery verification diagnostics and require receipt inspection before a
manual resend. For scheduled jobs with both a stable job ID and due time, the
hook also passes those values to the plugin. The SHA-256-keyed delivery ledger
stores a request UUID for that job occurrence and target chat. The send claim
atomically changes from pending to unknown under the cross-process lock before
network I/O, so two Cron workers racing on one occurrence make at most one
outbound call. A verified receipt is reused without another send; a prior unknown
outcome is held for inspection; a confirmed rejection permits a fresh UUID. An
occurrence without both fields
uses the immediate ambiguous-outcome guard but has no durable Cron dedup key.
The ledger retains unresolved attempts until a verified terminal outcome; only terminal
entries expire after seven days. At 1,024 unresolved attempts, new sends are held while
existing evidence remains intact. `doctor --json` reports the unresolved count, the oldest
unresolved age, expired pending attempts, and remaining unresolved capacity without exposing
message content or target identifiers.

## Native Hermes hooks

Version 0.16.0 registers current Hermes streaming/tool/approval observers through the package entry
point. The callbacks only increment local counters and return immediately; they never render cards,
route messages or inspect/store stream text. Use `doctor --json` to see the exact hook set reported
by the installed Hermes runtime.

## SDK recovery boundary

`doctor` feature-probes the constructors used by the plugin instead of trusting only a package
version. `repair-sdk` is explicit and targets `sys.executable` through `uv pip`, preventing a repair
from landing in a different user/system Python. The official `lark-cli` remains an optional
preflight tool and the official `lark-channel-sdk` remains an evaluated migration target.
