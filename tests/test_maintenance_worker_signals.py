"""Guard against workers shadowing ``QThread.finished``.

A worker that declares its own ``finished`` signal hides the one Qt emits when
the thread has actually stopped.  Handlers then fire from inside ``run()``, and
anything that drops the worker reference there destroys a still-running
QThread — "QThread: Destroyed while thread is still running".

These tests assert the shape rather than the timing: the real signal must stay
reachable on every worker, and the dialog must own each one via ``_own_worker``.
"""
import inspect

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QThread

import app.ui.db_maintenance_dialog as dbm


def _worker_classes():
    return [
        obj for _name, obj in inspect.getmembers(dbm, inspect.isclass)
        if issubclass(obj, QThread) and obj is not QThread
        and obj.__module__ == dbm.__name__
    ]


def test_module_defines_the_expected_workers():
    names = {c.__name__ for c in _worker_classes()}
    assert names == {
        "VerifyWorker", "SearchWorker", "DHashWorker", "EmbeddingWorker",
        "_WarningRecheckWorker", "ImageWarningWorker", "PresupportWarningWorker",
        "CompactWorker", "DedupWorker", "PruneImagesWorker",
        "FindDuplicateEntriesWorker", "MovedInDbWorker",
    }


@pytest.mark.parametrize("worker_cls", _worker_classes(), ids=lambda c: c.__name__)
def test_worker_does_not_shadow_qthread_finished(worker_cls):
    """``finished`` must still be Qt's own signal, not a redeclared one."""
    assert "finished" not in vars(worker_cls), (
        f"{worker_cls.__name__} declares its own 'finished' signal, hiding "
        "QThread.finished — name the result signal something else"
    )


@pytest.mark.parametrize("worker_cls", _worker_classes(), ids=lambda c: c.__name__)
def test_worker_declares_a_distinct_result_signal(worker_cls):
    """Each worker reports its results through a *_finished signal.

    Walks the MRO, since a worker may inherit the signal from a shared base
    (``_WarningRecheckWorker``) rather than declaring it itself.
    """
    result_signals = [
        n for klass in worker_cls.__mro__ for n in vars(klass) if n.endswith("_finished")
    ]
    assert result_signals, f"{worker_cls.__name__} has no *_finished result signal"


def test_every_worker_is_started_through_own_worker():
    """No start path may assign a worker attribute directly."""
    source = inspect.getsource(dbm.DbMaintenanceDialog)
    offenders = [
        line.strip() for line in source.splitlines()
        if "_worker = " in line and "_own_worker" not in line
        and "None" not in line and "getattr" not in line
    ]
    assert not offenders, f"workers assigned outside _own_worker(): {offenders}"


def test_own_worker_parents_and_releases_on_real_finished():
    """_own_worker parents the thread and clears the attribute on QThread.finished."""
    source = inspect.getsource(dbm.DbMaintenanceDialog._own_worker)
    assert "setParent(self)" in source
    assert "worker.finished.connect" in source
