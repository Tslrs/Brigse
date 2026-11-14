// Blinky for the Sipeed Tang Nano 9K (27 MHz clock).
// The board's button and LEDs are active low: pressing S1 pulls rst_n to 0, and a 0 lights an LED.
module blinky #(parameter WIDTH = 26) (
    input  wire       clk,
    input  wire       rst_n,
    output wire [3:0] led
);
    reg [WIDTH-1:0] count = 0;
    always @(posedge clk)
        if (!rst_n) count <= 0;
        else        count <= count + 1'b1;
    // Top four bits: the slowest LED changes about every 1.2 s, the fastest about every 0.16 s.
    assign led = ~count[WIDTH-1:WIDTH-4];
endmodule
