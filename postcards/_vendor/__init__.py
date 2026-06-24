"""Vendored third-party packages.

This directory holds in-tree copies of upstream packages that do not
install cleanly on the supported Python versions (3.12 / 3.13) and that
we cannot easily replace. See ``postcards._vendor.postcard_creator`` for
the shim that replaces ``postcard-creator`` (PyPI package name
``postcard-creator``, import name ``postcard_creator``).

The shims are imported under their qualified name
(``postcards._vendor.postcard_creator``) inside the ``postcards``
package; they are not placed on ``sys.path``. This avoids accidentally
shadowing a system-installed copy of the same upstream package and
keeps the import graph explicit.

See ``docs/CONSTITUTION.md`` §1 for why the live Swiss Post backend is
not exercised by CI.
"""

__all__ = ["postcard_creator"]
