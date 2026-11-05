# XVB for Arch Linux and Arch-based distributions

This directory contains the native Arch package recipe for XVB.

It is intended for:
- Arch Linux
- EndeavourOS
- Manjaro
- CachyOS

The same PKGBUILD is used on all four distributions. No distribution-specific
fork of the player is required.

## Build and install

Install the standard build tools if they are not already present:

```bash
sudo pacman -S --needed base-devel git
```

Download the repository branch containing the packaging files:

```bash
git clone --branch arch-packaging https://github.com/JonaDev2026/xvb.git
cd xvb/packaging/arch
makepkg -si
```

Alternatively, download this directory together with the PKGBUILD and run
`makepkg -si` from inside it.

## Dependencies

The PKGBUILD declares the runtime dependencies required by XVB:
Python, Tk, mpv/libmpv, python-mpv, Pillow, Mutagen, librsvg and
fontconfig.

Optional integrations:
- FFmpeg for additional media processing.
- python-dbus and python-gobject for MPRIS desktop media integration.
- tkdnd for drag-and-drop support.

## Notes

- The package installs the application under `/usr/lib/xvb`.
- The `xvb` launcher is installed in `/usr/bin/xvb`.
- The desktop entry and application icons are installed system-wide.
- The Debian packaging script and Debian package workflow are not modified.
- The recipe downloads the versioned GitHub source archive; it does not package
  arbitrary working-tree changes from the main branch.

## Verify the package

Build the package with `makepkg -s`, then inspect and test the generated
package before distributing it. A successful build alone does not replace a
runtime test on Arch Linux.
