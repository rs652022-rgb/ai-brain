"""Opening the database while another thread writes must wait, not fail with 'database is locked'."""
import sqlite3
import threading
import time

from brain import config, db


def _hold_write_lock(path, release: threading.Event) -> None:
    conn = sqlite3.connect(path)
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("INSERT INTO x VALUES (1)")
    release.wait(5)
    conn.commit()
    conn.close()


def test_connect_waits_for_a_writer_on_a_new_database(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MULTIUSER", False, raising=False)
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    path = tmp_path / "brain.db"
    with sqlite3.connect(path) as setup:          # a brand-new database, not yet in WAL mode
        setup.execute("CREATE TABLE x (i INTEGER)")
    release = threading.Event()
    writer = threading.Thread(target=_hold_write_lock, args=(path, release))
    writer.start()
    time.sleep(0.2)
    threading.Timer(0.4, release.set).start()
    try:
        conn = db.connect()
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        conn.close()
    finally:
        release.set()
        writer.join()


def test_connect_keeps_wal_mode_once_set(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MULTIUSER", False, raising=False)
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    for _ in range(2):
        conn = db.connect()
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        conn.close()
