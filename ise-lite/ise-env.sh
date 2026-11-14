# Environment for the slim ISE 14.7 (x86-64 Linux).
export XILINX_DS=/opt/Xilinx/14.7/ISE_DS
export XILINX=$XILINX_DS/ISE
export XILINX_COMMON=$XILINX_DS/common
export PATH=$XILINX/bin/lin64:$XILINX_COMMON/bin/lin64:$PATH
export LD_LIBRARY_PATH=$XILINX/lib/lin64:$XILINX_COMMON/lib/lin64
