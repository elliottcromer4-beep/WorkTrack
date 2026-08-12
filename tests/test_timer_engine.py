from datetime import timedelta

from src.timer_engine import TimerEngine, TimerStatus
from src.timeutil import iso_utc, now_utc


def _project_and_task(db):
    project_id = db.create_project("P")
    return project_id, db.create_subtask(project_id, "T")


def test_start_pause_resume_stop(db):
    project_id, task_id = _project_and_task(db)
    timer = TimerEngine(db)

    timer.start(project_id, task_id)
    assert timer.status is TimerStatus.RUNNING
    assert timer.is_active

    timer.pause()
    assert timer.status is TimerStatus.PAUSED
    paused_at = timer.elapsed_seconds

    timer.resume()
    assert timer.status is TimerStatus.RUNNING
    assert timer.elapsed_seconds >= paused_at

    session_id = timer.stop()
    assert timer.status is TimerStatus.STOPPED
    assert db.get_session(session_id).is_running == 0


def test_paused_time_is_not_billed(db):
    project_id, task_id = _project_and_task(db)
    timer = TimerEngine(db)
    timer.start(project_id, task_id)

    # Pretend the session started an hour ago and was paused for forty minutes.
    timer._started_at = now_utc() - timedelta(hours=1)
    timer._paused_duration = 40 * 60

    assert 19 * 60 <= timer.elapsed_seconds <= 21 * 60


def test_starting_a_second_timer_closes_the_first(db):
    project_id, first = _project_and_task(db)
    second = db.create_subtask(project_id, "T2")
    timer = TimerEngine(db)

    timer.start(project_id, first)
    first_id = timer.session_id
    timer.start(project_id, second)

    assert db.get_session(first_id).is_running == 0
    assert timer.session_id != first_id


def test_stop_when_idle_is_harmless(db):
    timer = TimerEngine(db)
    assert timer.stop() is None
    assert timer.elapsed_seconds == 0.0


def test_state_survives_a_restart(db):
    project_id, task_id = _project_and_task(db)
    timer = TimerEngine(db)
    timer.start(project_id, task_id)
    timer._started_at = now_utc() - timedelta(minutes=10)
    timer._persist()

    restored = TimerEngine(db)
    assert restored.status is TimerStatus.RUNNING
    assert restored.session_id == timer.session_id
    assert 9 * 60 <= restored.elapsed_seconds <= 11 * 60


def test_abandoned_session_is_closed_off_at_its_last_heartbeat(db):
    """A machine switched off on Friday must not bill the weekend."""
    project_id, task_id = _project_and_task(db)
    timer = TimerEngine(db)
    timer.start(project_id, task_id)
    session_id = timer.session_id

    started = now_utc() - timedelta(days=3)
    last_seen = started + timedelta(hours=2)
    db._conn.execute(
        "UPDATE timer_state SET started_at=?, updated_at=? WHERE id=1",
        (iso_utc(started), iso_utc(last_seen)),
    )
    db._conn.execute("UPDATE sessions SET started_at=? WHERE id=?",
                     (iso_utc(started), session_id))
    db.commit()

    recovered = TimerEngine(db)
    assert recovered.status is TimerStatus.STOPPED
    assert recovered.recovered_session_seconds == 2 * 3600

    session = db.get_session(session_id)
    assert session.duration_seconds == 2 * 3600
    assert session.is_running == 0


def test_a_recent_heartbeat_keeps_the_timer_running(db):
    project_id, task_id = _project_and_task(db)
    timer = TimerEngine(db)
    timer.start(project_id, task_id)
    timer.heartbeat()

    restored = TimerEngine(db)
    assert restored.status is TimerStatus.RUNNING
    assert restored.recovered_session_seconds is None


def test_toggle_cycles_through_the_states(db):
    project_id, task_id = _project_and_task(db)
    timer = TimerEngine(db)

    timer.toggle(project_id, task_id)
    assert timer.status is TimerStatus.RUNNING
    timer.toggle()
    assert timer.status is TimerStatus.PAUSED
    timer.toggle()
    assert timer.status is TimerStatus.RUNNING


def test_toggle_without_a_task_does_nothing(db):
    timer = TimerEngine(db)
    timer.toggle()
    assert timer.status is TimerStatus.STOPPED


def test_a_broken_listener_does_not_break_the_timer(db):
    project_id, task_id = _project_and_task(db)
    timer = TimerEngine(db)
    seen = []

    timer.on_change(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    timer.on_change(lambda: seen.append(True))

    timer.start(project_id, task_id)
    assert seen  # the second listener still ran
