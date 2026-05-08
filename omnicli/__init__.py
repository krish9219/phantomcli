"""omnicli — frozen v3 surface.

This package is the v3 PhantomCLI codebase. ADR-0002 freezes its public
API: bug fixes are accepted, additions are not. New behaviour lands in the
co-resident ``phantom`` package.

The version reported here is the package-as-shipped version; it is bumped
when a v3 patch lands. ``phantom._version.__version__`` is the v4 dev
version and bumps independently.
"""

__version__ = "1.0.2"
