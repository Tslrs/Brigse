// A design that uses several kinds of tiles on the Tang Nano 9K, so the chip view has something to show:
//   * LANES shift-register generators                      -> logic tiles (LUTs and flip-flops)
//   * a 4096 x 16 memory                                   -> block RAM (BSRAM)
//   * an 18 x 18 bit multiplier                            -> a DSP (multiplier) tile
// Everything feeds the four LEDs so that nothing is optimised away. It does not do anything useful.
module demo #(parameter LANES = 64) (
    input  wire       clk,
    input  wire       rst_n,
    output wire [3:0] led
);
    // logic
    wire [LANES-1:0] bits;
    genvar i;
    generate
        for (i = 0; i < LANES; i = i + 1) begin : lane
            reg [31:0] s;
            always @(posedge clk)
                if (!rst_n) s <= 32'h1 + i * 32'h9E3779B9;
                else        s <= {s[30:0], s[31] ^ s[21] ^ s[1] ^ s[0]};
            assign bits[i] = s[0] ^ s[17];
        end
    endgenerate

    // block RAM
    reg [15:0] mem [0:4095];
    reg [11:0] addr;
    reg [15:0] rdata;
    always @(posedge clk) begin
        addr  <= rst_n ? addr + 1'b1 : 12'd0;
        mem[addr] <= {addr[7:0], rdata[7:0]} ^ bits[15:0];
        rdata <= mem[addr + 12'd3];
    end

    // multiplier
    reg [17:0] a, b;
    reg [35:0] p;
    always @(posedge clk) begin
        a <= rst_n ? a + 18'd37 : 18'd1;
        b <= rst_n ? b + 18'd91 : 18'd1;
        p <= a * b;
    end

    assign led = ~{^bits, ^rdata, ^p, bits[0]};     // the LEDs are active low
endmodule
