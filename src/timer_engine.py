"""
The timer state machine: STOPPED → RUNNING ⇄ PAUSED → STOPPED.

Elapsed time is derived from wall-clock timestamps rather than counted by a
ticking loop, so it cannot drift and it survives the app being closed.  A
heartbeat records that the timer was still being watched; if the app dies and
comes back much later, the session is closed off at the last heartbeat instead
of silently billing the hours the machine spent switched off.
"""
from datetime import datetime
from enum import Enum
from typing import Callable, Optional

from .database import Database
from .models import TimerStateRecord
from .timeutil import iso_utc, now_utc, parse_utc


class TimerStatus(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"


class TimerEngine:
    #: How often the running timer records that it is still being watched.
    HEARTBEAT_SECONDS = 30

    #: A restored RUNNING timer whose heartbeat is older than this is assumed
    #: to have been abandoned — the app crashed, or the machine was shut down.
    STALE_AFTER_SECONDS = 5 * 60

    def __init__(self, db: Database):
        self.db = db
        self.status = TimerStatus.STOPPED
        self.session_id: Optional[int] = None
        self.project_id: Optional[int] = None
        self.subtask_id: Optional[int] = None
        self._started_at: Optional[datetime] = None
        self._paused_at: Optional[datetime] = None
        self._paused_duration: float = 0.0
        self._change_callbacks: list[Callable] = []
        #: Set when a restored session had to be closed off at its heartbeat,
        #: so the UI can tell the user rather than leaving them to notice.
        self.recovered_session_seconds: Optional[float] = None
        self._restore()

    # ── State ─────────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self.status == TimerStatus.RUNNING

    @property
    def is_paused(self) -> bool:
        return self.status == TimerStatus.PAUSED

    @property
    def is_active(self) -> bool:
        return self.status != TimerStatus.STOPPED

    @property
    def elapsed_seconds(self) -> float:
        if self.status == TimerStatus.STOPPED or self._started_at is None:
            return 0.0
        end = self._paused_at if self.status == TimerStatus.PAUSED else now_utc()
        end = end or now_utc()
        return max(0.0, (end - self._started_at).total_seconds() - self._paused_duration)

    # ── Controls ──────────────────────────────────────────────────────────

    def start(self, project_id: int, subtask_id: int):
        """Start a new session, finalising any session already in progress."""
        if self.status != TimerStatus.STOPPED:
            self.stop()

        started = now_utc()
        self.session_id = self.db.create_session(project_id, subtask_id, started)
        self.status = TimerStatus.RUNNING
        self.project_id = project_id
        self.subtask_id = subtask_id
        self._started_at = started
        self._paused_at = None
        self._paused_duration = 0.0

        self._persist()
        self._notify()

    def pause(self):
        if self.status != TimerStatus.RUNNING:
            return
        self._paused_at = now_utc()
        self.status = TimerStatus.PAUSED
        self._persist()
        self._notify()

    def resume(self):
        if self.status != TimerStatus.PAUSED:
            return
        if self._paused_at:
            self._paused_duration += (now_utc() - self._paused_at).total_seconds()
        self._paused_at = None
        self.status = TimerStatus.RUNNING
        self._persist()
        self._notify()

    def stop(self) -> Optional[int]:
        """Finalise the current session.  Returns its id, or None if idle."""
        if self.status == TimerStatus.STOPPED:
            return None

        elapsed = self.elapsed_seconds
        ended_at = self._paused_at if self.status == TimerStatus.PAUSED else now_utc()
        return self._finalise(ended_at or now_utc(), elapsed)

    def toggle(self, project_id: Optional[int] = None, subtask_id: Optional[int] = None):
        """Start if idle, pause if running, resume if paused."""
        if self.status == TimerStatus.STOPPED:
            if project_id and subtask_id:
                self.start(project_id, subtask_id)
        elif self.status == TimerStatus.RUNNING:
            self.pause()
        else:
            self.resume()

    def heartbeat(self):
        """Record that a running timer is still being watched."""
        if self.status == TimerStatus.RUNNING:
            self._persist()

    # ── Callbacks ─────────────────────────────────────────────────────────

    def on_change(self, callback: Callable):
        self._change_callbacks.append(callback)

    def remove_callback(self, callback: Callable):
        if callback in self._change_callbacks:
            self._change_callbacks.remove(callback)

    def _notify(self):
        for callback in list(self._change_callbacks):
            try:
                callback()
            except Exception:  # a broken listener must not stop the timer
                pass

    # ── Persistence ───────────────────────────────────────────────────────

    def _finalise(self, ended_at: datetime, elapsed: float) -> Optional[int]:
        finished_id = self.session_id
        if finished_id:
            self.db.finalize_session(finished_id, ended_at, elapsed,
                                     self._paused_duration)

        self.status = TimerStatus.STOPPED
        self.session_id = None
        self.project_id = None
        self.subtask_id = None
        self._started_at = None
        self._paused_at = None
        self._paused_duration = 0.0

        self.db.clear_timer_state()
        self._notify()
        return finished_id

    def _persist(self):
        self.db.save_timer_state(TimerStateRecord(
            session_id=self.session_id,
            status=self.status.value,
            project_id=self.project_id,
            subtask_id=self.subtask_id,
            started_at=iso_utc(self._started_at) if self._started_at else None,
            paused_at=iso_utc(self._paused_at) if self._paused_at else None,
            paused_duration_seconds=self._paused_duration,
        ))

    def _restore(self):
        record = self.db.get_timer_state()
        if not record or record.status == "stopped" or not record.started_at:
            self.db.discard_running_sessions()
            return

        started = parse_utc(record.started_at)
        if started is None:
            self.db.clear_timer_state()
            self.db.discard_running_sessions()
            return

        self.status = TimerStatus(record.status)
        self.session_id = record.session_id
        self.project_id = record.project_id
        self.subtask_id = record.subtask_id
        self._started_at = started
        self._paused_at = parse_utc(record.paused_at)
        self._paused_duration = record.paused_duration_seconds

        if self.status == TimerStatus.RUNNING:
            self._close_off_if_abandoned(record)

    def _close_off_if_abandoned(self, record: TimerStateRecord):
        """
        Finalise a running session whose heartbeat has gone stale.

        Without this, an app that was killed on Friday evening and reopened on
        Monday would report the whole weekend as tracked work.
        """
        last_seen = parse_utc(record.updated_at)
        if last_seen is None or last_seen < self._started_at:
            return

        if (now_utc() - last_seen).total_seconds() <= self.STALE_AFTER_SECONDS:
            return

        elapsed = max(0.0, (last_seen - self._started_at).total_seconds()
                      - self._paused_duration)
        self.recovered_session_seconds = elapsed
        self._finalise(last_seen, elapsed)
