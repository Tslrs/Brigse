`timescale 1ns/1ps
// Self-checking testbench: with WIDTH=6 the LEDs must show the top four counter bits.
module tb_blinky;
    reg clk = 0, rst = 1;
    wire [3:0] led;
    blinky #(.WIDTH(6)) dut (.clk(clk), .rst(rst), .led(led));
    always #5 clk = ~clk;
    initial begin
        repeat (2) @(posedge clk);
        rst = 0;
        repeat (20) @(posedge clk);
        #1;
        if (led !== 4'd5) begin
            $display("FAIL: led=%0d, expected 5 after 20 clocks", led);
            $finish;
        end
        $display("PASS: led=%0d", led);
        $finish;
    end
endmodule
