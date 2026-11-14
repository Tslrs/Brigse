#!/bin/bash
# Full ISE 14.7 command-line build of the Next186 Spartan-3A design.
# usage: build.sh <project_dir> [top=system] [ucf=ddr_186.ucf] [part=xc3s700a-fg484-4]
source "$(dirname "$0")/ise-env.sh"
cd "$1" || exit 2
TOP=${2:-system}; UCF=${3:-ddr_186.ucf}; PART=${4:-xc3s700a-fg484-4}
mkdir -p xst/projnav.tmp _ngo
step() { local n=$1; shift; local s=$(date +%s); echo "### $n start $(date +%T)"; "$@" > "$n.log" 2>&1; local rc=$?; echo "### $n exit=$rc secs=$(( $(date +%s)-s ))"; [ $rc -eq 0 ] || { tail -15 "$n.log"; exit $rc; }; }
T0=$(date +%s)
step xst      xst -intstyle xflow -ifn $TOP.xst -ofn $TOP.syr
step ngdbuild ngdbuild -intstyle xflow -dd _ngo -sd ipcore_dir -nt timestamp -uc $UCF -p $PART $TOP.ngc $TOP.ngd
step map      map -intstyle xflow -p $PART -cm area -ir off -pr off -c 100 -o ${TOP}_map.ncd $TOP.ngd $TOP.pcf
step par      par -w -intstyle xflow -ol std -t 1 ${TOP}_map.ncd $TOP.ncd $TOP.pcf
step trce     trce -intstyle xflow -v 3 -s 4 -n 3 -fastpaths -xml $TOP.twx $TOP.ncd -o $TOP.twr $TOP.pcf -ucf $UCF
step bitgen   bitgen -intstyle xflow -f $TOP.ut $TOP.ncd
echo "### DONE total_secs=$(( $(date +%s)-T0 ))"; ls -la $TOP.bit
