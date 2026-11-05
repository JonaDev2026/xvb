# XVB Arch packaging branch

This branch contains only the files needed to build and publish the native Arch package.

- `.github/workflows/arch-package.yml`: builds the package from a published XVB release.
- `packaging/arch/PKGBUILD`: Arch package recipe.
- `packaging/arch/.SRCINFO`: generated package metadata.
- `packaging/arch/README.md`: build and installation instructions.
- `LICENSE`: project license.

The application source is downloaded by the PKGBUILD from the versioned GitHub release archive. It is deliberately not duplicated in this branch.

For the application source and full project documentation, see the [main branch](https://github.com/JonaDev2026/xvb/tree/main).
