"""MQTT → Postgres ingest worker.

Run: python -m dashboard.ingest

Reliability model (see README "Ingest reliability"):

* every persist goes through :class:`DbGuard`, which owns the only psycopg
  connection, reconnects when it is closed/broken and retries the message once;
* repeated failure (``INGEST_MAX_DB_FAILURES`` consecutive failed persists, or
  failures lasting ``INGEST_STALL_SECONDS``) is fatal: the worker logs ERROR and
  exits 1 so Railway's ON_FAILURE restart policy brings it back clean.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import paho.mqtt.client as mqtt
import psycopg

from dashboard.db import Stored, connect, ensure_schema, store_message
from dashboard.settings import Settings, load_settings

log = logging.getLogger("dashboard.ingest")

DEFAULT_MAX_DB_FAILURES = 5
DEFAULT_STALL_SECONDS = 1800.0
DEFAULT_PROBE_AFTER = 300.0
HARD_EXIT_GRACE_SECONDS = 15.0


class IngestFatal(RuntimeError):
    """The database is unusable; the process must exit non-zero."""


class DbGuard:
    """One self-healing DB connection plus failure bookkeeping.

    * ``persist`` reconnects when the connection is closed or raises
      ``OperationalError`` / ``InterfaceError`` and retries the message once.
    * If the last successful DB operation is older than ``probe_after`` seconds a
      cheap ``SELECT 1`` runs before the insert, so a connection that went stale
      while idle is replaced before it costs a message.
    * A persist that still fails counts as a failure. ``max_failures``
      consecutive failures, or failures spanning ``stall_seconds`` (measured from
      the first failure of the run, so a quiet hour is not a stall), raise
      :class:`IngestFatal`.
    * A ``DataError`` (e.g. a payload Postgres rejects) is the message's fault,
      not the database's: it is logged and dropped without counting.
    """

    def __init__(
        self,
        database_url: str,
        *,
        max_failures: int = DEFAULT_MAX_DB_FAILURES,
        stall_seconds: float = DEFAULT_STALL_SECONDS,
        probe_after: float = DEFAULT_PROBE_AFTER,
        connect_fn: Callable[[str], psycopg.Connection] = connect,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.database_url = database_url
        self.max_failures = max(1, max_failures)
        self.stall_seconds = stall_seconds
        self.probe_after = probe_after
        self._connect = connect_fn
        self._clock = clock
        self.conn: psycopg.Connection | None = None
        self.failures = 0
        self.first_failure_at: float | None = None
        self.last_ok_at: float = clock()
        self.reconnects = 0

    # -- connection -------------------------------------------------------
    def open(self, device_ids: tuple[str, ...] = ()) -> None:
        """Connect and apply the schema (startup; errors propagate)."""
        self.conn = self._connect(self.database_url)
        ensure_schema(self.conn, device_ids)
        self.last_ok_at = self._clock()

    def close(self) -> None:
        self._drop()

    def _drop(self) -> None:
        conn, self.conn = self.conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def _connection(self) -> psycopg.Connection:
        if self.conn is None or self.conn.closed:
            self._drop()
            self.conn = self._connect(self.database_url)
            self.reconnects += 1
            self.last_ok_at = self._clock()
            log.info("db connected (reconnects=%d)", self.reconnects)
        return self.conn

    def _probe(self, conn: psycopg.Connection) -> None:
        if self._clock() - self.last_ok_at <= self.probe_after:
            return
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        conn.commit()
        self.last_ok_at = self._clock()

    # -- bookkeeping ------------------------------------------------------
    def _mark_ok(self) -> None:
        self.failures = 0
        self.first_failure_at = None
        self.last_ok_at = self._clock()

    def _mark_failed(self, exc: BaseException) -> None:
        now = self._clock()
        self.failures += 1
        if self.first_failure_at is None:
            self.first_failure_at = now
        stalled_for = now - self.first_failure_at
        log.error(
            "persist failed (%d consecutive, failing for %.0fs): %s: %s",
            self.failures,
            stalled_for,
            type(exc).__name__,
            exc,
        )
        if self.failures >= self.max_failures:
            raise IngestFatal(
                f"{self.failures} consecutive DB failures (limit {self.max_failures})"
            ) from exc
        if stalled_for >= self.stall_seconds:
            raise IngestFatal(
                f"no successful persist for {stalled_for:.0f}s "
                f"(limit {self.stall_seconds:.0f}s) while messages keep arriving"
            ) from exc

    # -- API --------------------------------------------------------------
    def persist(
        self, topic: str, received_at: datetime, payload: dict[str, Any]
    ) -> Stored | None:
        """Store one message. ``None`` = nothing stored (dropped or failed).

        Raises :class:`IngestFatal` when the failure thresholds are crossed.
        """
        last_exc: BaseException | None = None
        for attempt in (1, 2):
            try:
                conn = self._connection()
                self._probe(conn)
                stored = store_message(
                    conn, topic=topic, received_at=received_at, payload=payload
                )
            except psycopg.DataError as exc:
                log.error("dropping message topic=%s: database rejected it: %s", topic, exc)
                self._mark_ok()  # the database answered
                return None
            except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
                last_exc = exc
                log.warning(
                    "db connection problem (attempt %d/2): %s: %s",
                    attempt,
                    type(exc).__name__,
                    exc,
                )
                self._drop()
                continue
            except psycopg.Error as exc:
                last_exc = exc
                break  # not a connection problem; a retry would fail the same way
            except Exception:
                # a bug/oddity in extraction for this one payload: skip it
                log.exception("dropping message topic=%s: unexpected error", topic)
                return None
            if stored is None:
                log.warning("ignore topic=%s (no IMEI and cannot parse device id)", topic)
            self._mark_ok()
            return stored
        assert last_exc is not None
        self._mark_failed(last_exc)
        return None


def _make_client(client_id: str) -> mqtt.Client:
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    except Exception:
        return mqtt.Client(client_id=client_id)


def _env_number(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning("ignoring invalid %s=%r (using %s)", name, raw, default)
        return default


def _parse_payload(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw}
    return payload if isinstance(payload, dict) else {"_raw": payload}


class IngestWorker:
    """Wires MQTT messages to :class:`DbGuard` and owns the exit decision."""

    def __init__(
        self,
        guard: DbGuard,
        client: Any,
        *,
        hard_exit_after: float | None = HARD_EXIT_GRACE_SECONDS,
    ) -> None:
        self.guard = guard
        self.client = client
        self.hard_exit_after = hard_exit_after
        self.stopping = False
        self.exit_code = 0

    def handle(self, topic: str, raw_bytes: bytes) -> None:
        received_at = datetime.now(timezone.utc)
        payload = _parse_payload(raw_bytes.decode("utf-8", "replace"))
        try:
            stored = self.guard.persist(topic, received_at, payload)
        except IngestFatal as exc:
            self.fail(str(exc))
            return
        if stored is None:
            return  # DbGuard already logged why
        log.info(
            "uplink device=%s kind=%s readings=%d imei=%s battery=%s signal=%s model=%s",
            stored.device_id,
            stored.kind,
            stored.readings,
            payload.get("IMEI"),
            payload.get("battery"),
            payload.get("signal"),
            payload.get("Model"),
        )

    def stop(self) -> None:
        self.stopping = True
        self.client.disconnect()

    def fail(self, reason: str) -> None:
        """Fatal DB condition: make ``main`` return 1.

        Runs in paho's network thread (``loop_forever`` calls callbacks on the
        thread that called it), so it cannot just raise: set the flags and
        ``disconnect()`` so ``loop_forever`` returns and ``main`` returns 1. If
        the MQTT socket is wedged and the loop never returns, a daemon timer
        forces ``os._exit(1)`` after a short grace period (logging flushed first).
        """
        log.error("fatal: %s; exiting with code 1 so the platform restarts the service", reason)
        self.exit_code = 1
        self.stopping = True
        if self.hard_exit_after is not None:
            timer = threading.Timer(self.hard_exit_after, _hard_exit, args=(1,))
            timer.daemon = True
            timer.start()
        try:
            self.client.disconnect()
        except Exception:
            pass


def _hard_exit(code: int) -> None:
    log.error("main loop did not stop after fatal error; forcing exit %d", code)
    logging.shutdown()
    try:
        sys.stdout.flush()
    except Exception:
        pass
    os._exit(code)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )
    settings: Settings = load_settings(ingest_defaults=True)
    max_failures = int(_env_number("INGEST_MAX_DB_FAILURES", DEFAULT_MAX_DB_FAILURES))
    stall_seconds = _env_number("INGEST_STALL_SECONDS", DEFAULT_STALL_SECONDS)

    log.info(
        "ingest starting mqtt=%s:%s topic=%s db=%s seeds=%s max_db_failures=%d stall_s=%.0f",
        settings.mqtt_host,
        settings.mqtt_port,
        settings.mqtt_topic,
        settings.database_url.split("@")[-1],
        ",".join(settings.device_ids),
        max_failures,
        stall_seconds,
    )

    guard = DbGuard(
        settings.database_url, max_failures=max_failures, stall_seconds=stall_seconds
    )
    try:
        guard.open(settings.device_ids)
    except Exception:
        log.exception("cannot open database at startup")
        return 1

    client = _make_client(f"dashboard-ingest-{os.getpid()}")
    if settings.mqtt_user and settings.mqtt_pass:
        client.username_pw_set(settings.mqtt_user, settings.mqtt_pass)
    worker = IngestWorker(guard, client)

    def handle_stop(*_args: object) -> None:
        log.info("shutdown requested")
        worker.stop()

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    def on_connect(client: mqtt.Client, _u: object, _f: object, rc: object, _p: object = None) -> None:
        code = rc if isinstance(rc, int) else getattr(rc, "value", rc)
        log.info("mqtt connect rc=%s", code)
        if code != 0:
            return
        client.subscribe(settings.mqtt_topic)
        log.info("subscribed %s", settings.mqtt_topic)

    def on_message(_c: mqtt.Client, _u: object, msg: mqtt.MQTTMessage) -> None:
        worker.handle(msg.topic, msg.payload)

    def on_disconnect(client: mqtt.Client, _u: object, _c: object, rc: object, _p: object = None) -> None:
        code = rc if isinstance(rc, int) else getattr(rc, "value", rc)
        log.warning("mqtt disconnect rc=%s", code)

    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    backoff = 1.0
    while not worker.stopping:
        try:
            client.connect(settings.mqtt_host, settings.mqtt_port, keepalive=60)
            backoff = 1.0
            client.loop_forever()
        except Exception:
            if worker.stopping:
                break
            log.exception("mqtt connection error; retry in %.1fs", backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    guard.close()
    log.info("ingest stopped (exit code %d)", worker.exit_code)
    return worker.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
