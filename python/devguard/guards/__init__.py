"""Guards.

Each guard module exposes ``NAME`` (its settings key, and the name
``<P>_DEVGUARD_DISABLE`` takes) and ``install()``. ``install()`` registers
post-import hooks or patches the standard library; it never imports the
framework itself, so importing a guard where there is no framework is free.

The core's are ``egress`` and ``mail_stdlib``. The product's sit beside them,
listed in ``_product.GUARDS``.
"""
