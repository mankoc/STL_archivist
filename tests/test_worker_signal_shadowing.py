"""App-wide guard: no QThread subclass may redeclare ``finished``.

``test_maintenance_worker_signals.py`` covers the Database Maintenance dialog
in depth; this extends the core rule to every worker in the application.  A
redeclared ``finished`` hides the signal Qt emits once the thread has really
stopped, so result handlers run from inside ``run()`` — and anything that
drops the worker there can destroy a still-running thread.
"""
import importlib
import inspect
import pkgutil

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QThread

import app


def _all_qthread_subclasses():
    found = {}
    for info in pkgutil.walk_packages(app.__path__, prefix="app."):
        module = importlib.import_module(info.name)
        for _name, obj in inspect.getmembers(module, inspect.isclass):
            if (issubclass(obj, QThread) and obj is not QThread
                    and obj.__module__ == module.__name__):
                found[f"{obj.__module__}.{obj.__qualname__}"] = obj
    return found


_WORKERS = _all_qthread_subclasses()


def test_workers_were_discovered():
    assert len(_WORKERS) >= 30, sorted(_WORKERS)


@pytest.mark.parametrize("qualname", sorted(_WORKERS))
def test_no_worker_shadows_qthread_finished(qualname):
    cls = _WORKERS[qualname]
    offenders = [k.__name__ for k in cls.__mro__
                 if k is not QThread and "finished" in vars(k)]
    assert not offenders, (
        f"{qualname} (via {offenders}) redeclares 'finished', hiding "
        "QThread.finished — name the result signal <something>_finished"
    )
