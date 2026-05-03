"""Pure-Python service layer — no Qt, no UI imports.

Modules in this package implement business operations that previously lived
inside Qt slots in :mod:`app.ui.browser_tab` and friends.  Extracting them
gives us:

* unit-testability without spinning up a ``QApplication``,
* a clean seam between the UI (collect inputs, show feedback) and the work
  itself (filesystem and database mutation).
"""
