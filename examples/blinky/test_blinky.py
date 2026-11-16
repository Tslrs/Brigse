"""A cocotb test for blinky.v. Run it through the cocotb_test tool, with parameters {"WIDTH": 6}:
the real counter is 26 bits wide, far too slow to simulate; with 6 bits the LEDs move every 4 clocks.
"""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge


async def start(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1
    await ClockCycles(dut.clk, 2)
    dut.rst.value = 0


@cocotb.test()
async def counts_up(dut):
    await start(dut)
    seen = []
    for _ in range(40):
        await RisingEdge(dut.clk)
        if not seen or seen[-1] != int(dut.led.value):
            seen.append(int(dut.led.value))
    assert seen[:4] == [0, 1, 2, 3], f"the LEDs should count 0,1,2,3 but gave {seen[:4]}"


@cocotb.test()
async def reset_clears(dut):
    await start(dut)
    await ClockCycles(dut.clk, 20)
    dut.rst.value = 1
    await ClockCycles(dut.clk, 2)
    assert int(dut.led.value) == 0, "reset should put the LEDs back to 0"
