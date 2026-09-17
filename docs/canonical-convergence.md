# Canonical convergence notes

Historical planning notes for converging older installs onto the public product
tree. New operators should ignore this file and use [INSTALL.md](INSTALL.md).

## Rules that remain true

1. Public product code must not depend on any private developer machine.
2. Do not copy private configuration, media, host addresses, or evidence
   bundles into the Git repository.
3. Operator library and playlist locations migrate as **data only**.
4. Production runtime root is `/opt/pifm` unless `PIFM_PREFIX` overrides it.
5. Fresh installs boot OFF AIR.

## Legacy layouts

Older home-directory trees, ad-hoc services, or seasonal demo media are
operator-local artifacts. They are not part of the distributable product.
