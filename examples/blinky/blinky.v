// Minimal example: divide the clock and drive four LEDs.
module blinky #(parameter WIDTH = 26) (
    input  wire       clk,
    input  wire       rst,
    output wire [3:0] led
);
    reg [WIDTH-1:0] count = 0;
    always @(posedge clk)
        if (rst) count <= 0;
        else     count <= count + 1'b1;
    assign led = count[WIDTH-1:WIDTH-4];
endmodule
