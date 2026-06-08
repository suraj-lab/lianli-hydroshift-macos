# tinyuz bridge vendor subset

This directory contains the minimal source subset needed to build `libtinyuz` for
HydroShift wireless RGB packets.

Sources:

- `tinyuz`: https://github.com/sisong/tinyuz — MIT License
- `HDiffPatch`: https://github.com/sisong/HDiffPatch — MIT License, includes libdivsufsort license text
- `tuz_wrapper.cpp`: copied from `sgtaziz/lian-li-linux`, an MIT wrapper around tinyuz's stream API

Only the compression files required by `scripts/build-tinyuz-lib.sh` are included.
The generated shared library is intentionally ignored by git.
