\ File written by HiGHS .lp file handler
min
 obj: -1 demand_load_1_1_1_1 -1 demand_load_2_1_1_1 +0.02 generator_generation_1_1_1_1 +0.035 generator_generation_2_1_1_1  +250
st
 bus_balance_1_1_1_1: +1 generator_generation_1_1_1_1 -1 line_flowp_1_1_1_1 -1 line_flowp_2_1_1_1 = +0
 bus_balance_2_1_1_1: +1 generator_generation_2_1_1_1 +1 line_flowp_1_1_1_1 -0.816326530612245 line_flowp_3_1_1_1 -1 line_flowp_4_1_1_1 = +0
 bus_balance_3_1_1_1: -1 demand_load_1_1_1_1 +1 line_flowp_2_1_1_1 +0.816326530612245 line_flowp_3_1_1_1 -0.816326530612245 line_flowp_5_1_1_1 = +0
 bus_balance_4_1_1_1: -1 demand_load_2_1_1_1 +1 line_flowp_4_1_1_1 +0.816326530612245 line_flowp_5_1_1_1 = +0
 kirchhoff_cycle_0_1_1_1: -0.816326530612245 line_flowp_1_1_1_1 +0.816326530612245 line_flowp_2_1_1_1 -0.999583506872136 line_flowp_3_1_1_1 = -0
 kirchhoff_cycle_1_1_1_1: +0.816326530612245 line_flowp_1_1_1_1 -0.816326530612245 line_flowp_2_1_1_1 +0.816326530612245 line_flowp_4_1_1_1 -0.999583506872136 line_flowp_5_1_1_1 = -0
bounds
 demand_load_1_1_1_1 <= 150
 demand_load_2_1_1_1 <= 100
 generator_generation_1_1_1_1 <= 300
 generator_generation_2_1_1_1 <= 200
 -300 <= line_flowp_1_1_1_1 <= 300
 -300 <= line_flowp_2_1_1_1 <= 300
 -245 <= line_flowp_3_1_1_1 <= 245
 -200 <= line_flowp_4_1_1_1 <= 200
 -183.75 <= line_flowp_5_1_1_1 <= 183.75
end
