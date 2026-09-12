# custom-install
Installs a title directly to an SD card for the Nintendo 3DS. Originally created late June 2019.

## Summary

### Windows standalone

1. [Dump boot9.bin and movable.sed](https://wiki.hacks.guide/wiki/3DS:Dump_system_files) from a 3DS system.
2. Download the [latest releases](https://github.com/ihaveamac/custom-install/releases).
3. Extract and run ci-gui. Read `windows-quickstart.txt`.

### With installed Python

> [!NOTE]
> Windows users: Enabling "Add Python 3.X to PATH" is **NOT** required! Python is installed with the `py` launcher by default.

1. [Dump boot9.bin and movable.sed](https://ihaveamac.github.io/dump.html) from a 3DS system.
2. Install the packages:
  * Windows: `py -3 -m pip install --user --upgrade https://github.com/ihaveamac/custom-install/archive/refs/heads/python-package.zip`
  * macOS/Linux: `python3 -m pip install --user --upgrade https://github.com/ihaveamac/custom-install/archive/refs/heads/python-package.zip`

To run the GUI:
* Windows: `py -3 -m custominstall.gui`
* macOS/Linux: `python3 -m custominstall.gui`

To run the command line version:
* Windows: `py -3 -m custominstall`
* macOS/Linux: `python3 -m custominstall`

## Setup
Linux users must build [wwylele/save3ds](https://github.com/wwylele/save3ds) and place `save3ds_fuse` in one of these places:
* A directory in `PATH`
* In `custominstall/bin/linux`
* Set the environment variable `CUSTOM_INSTALL_SAVE3DS_PATH` to the `save3ds_fuse` binary

movable.sed is required and can be provided with `-m` or `--movable`.

boot9 is needed:
* `-b` or `--boot9` argument (if set)
* `BOOT9_PATH` environment variable (if set)
* `%APPDATA%\3ds\boot9.bin` (Windows-specific)
* `~/Library/Application Support/3ds/boot9.bin` (macOS-specific)
* `~/.3ds/boot9.bin`
* `~/3ds/boot9.bin`

A [SeedDB](https://github.com/ihaveamac/3DS-rom-tools/wiki/SeedDB-list) is needed for newer games (2015+) that use seeds.  
SeedDB is checked in order of:
* `-s` or `--seeddb` argument (if set)
* `SEEDDB_PATH` environment variable (if set)
* `%APPDATA%\3ds\seeddb.bin` (Windows-specific)
* `~/Library/Application Support/3ds/seeddb.bin` (macOS-specific)
* `~/.3ds/seeddb.bin`
* `~/3ds/seeddb.bin`

## custom-install-finalize
custom-install-finalize installs a ticket, plus a seed if required. This is required for the title to appear and function.

This can be built as most 3DS homebrew projects [with devkitARM](https://www.3dbrew.org/wiki/Setting_up_Development_Environment).

## Usage
Use `-h` to view arguments.

Examples:
```
py -3 -m custominstall -b boot9.bin -m movable.sed --sd E:\ file.cia file2.cia
python3 -m custominstall -b boot9.bin -m movable.sed --sd /Volumes/GM9SD file.cia file2.cia
python3 -m custominstall -b boot9.bin -m movable.sed --sd /media/GM9SD file.cia file2.cia
```

## Supported formats
Along with CIA files, cart dumps (`.cci`/`.3ds`), standalone NCCH files (`.cxi`/`.app`) and the
Z3DS-compressed versions of these formats (`.zcci`, `.zcxi`, `.zcia`, as used by Azahar) can be
installed. They are converted on the fly the same way as CCI to CIA converters: each NCCH
partition becomes an unencrypted content, and the SD application flag is set in the exheader.

Cart dumps with seed crypto need the matching seed in seeddb.bin.


## GUI
A GUI is provided to make the process easier.

### GUI Setup
Linux users may need to install a Tk package:
* Ubuntu/Debian: `sudo apt install python3-tk`
* Arch: `sudo pacman -S tk`
* Fedora: `sudo dnf install python3-tkinter`

## Development

### Building Windows standalone

> [!WARNING]
> This section is OUTDATED and currently does not work with the Python package setup.

## License/Credits
[save3ds by wwylele](https://github.com/wwylele/save3ds) is used to interact with the Title Database (details in `bin/README`).

Thanks to @nek0bit for redesigning `custominstall.py` to work as a module, and for implementing an earlier GUI.

Thanks to @LyfeOnEdge from the [brewtools Discord](https://brewtools.dev) for designing the second version of the GUI. Special thanks to CrafterPika and archbox for testing.

Thanks to @BpyH64 for [researching how to generate the cmacs](https://github.com/d0k3/GodMode9/issues/340#issuecomment-487916606).
