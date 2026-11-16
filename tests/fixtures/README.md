# Fixtures

Real output of the build tools, collected by breaking `examples/tangnano9k_blinky` and
`examples/blinky` on purpose (a missing semicolon, an unknown module, a mistyped signal, a pin that
does not exist, two signals on one pin, a port with no pin, an impossible clock). Paths were rewritten
to `/home/user/project`.

To keep them small: the Gowin logs keep only their ERROR/WARN lines, the yosys netlists keep only the
port list of the top module (all the checks read), and bitstreams are placeholders.

`ise_timing_multiclock` is the timing report of the Next186 build (six clocks, two of them failing), cut down to the heads of the per-clock blocks and the derived-constraint table.
