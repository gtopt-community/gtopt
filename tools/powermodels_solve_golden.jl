#!/usr/bin/env julia
# PowerModels.jl oracle for the cases/pm_case5_asym literature benchmark.
#
# Solves a MATPOWER case with PowerModels' lossless DC OPF
# (DCPPowerModel) and its DC-with-line-losses relaxation
# (DCPLLPowerModel) and dumps the objectives, dispatch and per-end
# branch flows as JSON.  gtopt's lossless run must match the DC
# objective; its piecewise-loss run with
# `line_thermal_limit_mode = sending` must approach the DCPLL objective
# as the number of loss segments grows
# (test/source/test_pm_case5_asym.cpp, cases/pm_case5_asym/README.md).
#
# The branch angle-difference limits are widened to ±ANGLE_DEG before
# solving: gtopt does not model angmin / angmax, and case5_asym's tight
# asymmetric limits bind.  The script fails if a widened limit still
# binds (pass a larger ANGLE_DEG then).
#
# Solvers: DC with HiGHS (an LP, exact vertex) and with Ipopt (the
# solver of PowerModels' own test/opf.jl); DCPLL only with Ipopt (its
# loss constraint `p_fr + p_to ≥ r·p_fr²` is a convex quadratic, which
# HiGHS does not accept).
#
# Usage (Julia env spec in tools/powermodels_goldens/Project.toml):
#   julia --project=tools/powermodels_goldens -e 'using Pkg; Pkg.instantiate()'
#   julia --project=tools/powermodels_goldens tools/powermodels_solve_golden.jl \
#       <case.m> <output_golden.json> [ANGLE_DEG=30]

using PowerModels
using JuMP
using Ipopt
using HiGHS
using JSON
using Pkg

PowerModels.silence()

function pkg_version(name::AbstractString)
    for (_, info) in Pkg.dependencies()
        if info.name == name
            return string(info.version)
        end
    end
    return "unknown"
end

function widen_angles!(data::Dict, angle_deg::Real)
    rad = deg2rad(angle_deg)
    for (_, br) in data["branch"]
        br["angmin"] = -rad
        br["angmax"] = rad
    end
    return data
end

function solve_one(data::Dict, form, optimizer)
    result = solve_opf(deepcopy(data), form, optimizer)
    status = string(result["termination_status"])
    sol = result["solution"]
    gens = Dict(
        k => g["pg"] * data["baseMVA"] for (k, g) in sol["gen"]
    )
    branches = Dict{String,Any}()
    max_angle = 0.0
    for (k, br) in sol["branch"]
        b = data["branch"][k]
        va_fr = sol["bus"][string(b["f_bus"])]["va"]
        va_to = sol["bus"][string(b["t_bus"])]["va"]
        dtheta = rad2deg(va_fr - va_to)
        max_angle = max(max_angle, abs(dtheta))
        pf = br["pf"] * data["baseMVA"]
        pt = br["pt"] * data["baseMVA"]
        # PowerModels' DCPLL loss: r * pf^2 in p.u. on the from-end flow.
        r_loss = b["br_r"] * br["pf"]^2 * data["baseMVA"]
        branches[k] = Dict(
            "f_bus" => b["f_bus"],
            "t_bus" => b["t_bus"],
            "pf_mw" => pf,
            "pt_mw" => pt,
            "loss_mw" => pf + pt,
            "r_pf2_mw" => r_loss,
            "rate_a_mw" => b["rate_a"] * data["baseMVA"],
            "dtheta_deg" => dtheta,
        )
    end
    return Dict(
        "termination_status" => status,
        "objective" => result["objective"],
        "gen_mw" => gens,
        "branch" => branches,
        "max_abs_dtheta_deg" => max_angle,
    )
end

function main(args)
    length(args) >= 2 || error("usage: <case.m> <out.json> [ANGLE_DEG]")
    case_path, out_path = args[1], args[2]
    angle_deg = length(args) >= 3 ? parse(Float64, args[3]) : 30.0

    data = PowerModels.parse_file(case_path)
    widen_angles!(data, angle_deg)

    ipopt = optimizer_with_attributes(
        Ipopt.Optimizer, "print_level" => 0, "tol" => 1e-10,
    )
    highs = optimizer_with_attributes(HiGHS.Optimizer, "output_flag" => false)

    results = Dict(
        "dc_highs" => solve_one(data, DCPPowerModel, highs),
        "dc_ipopt" => solve_one(data, DCPPowerModel, ipopt),
        "dcpll_ipopt" => solve_one(data, DCPLLPowerModel, ipopt),
    )
    for (name, r) in results
        println(stderr, "$(name): $(r["termination_status"]) ",
                "objective = $(r["objective"]) ",
                "max |dtheta| = $(r["max_abs_dtheta_deg"]) deg")
        if r["max_abs_dtheta_deg"] >= angle_deg - 1e-6
            error("$(name): a widened angle limit (±$(angle_deg)°) binds")
        end
    end

    out = Dict(
        "case" => basename(case_path),
        "angle_limit_deg" => angle_deg,
        "results" => results,
        "versions" => Dict(
            "julia" => string(VERSION),
            "PowerModels" => pkg_version("PowerModels"),
            "JuMP" => pkg_version("JuMP"),
            "Ipopt" => pkg_version("Ipopt"),
            "HiGHS" => pkg_version("HiGHS"),
        ),
    )
    open(out_path, "w") do io
        JSON.print(io, out, 2)
    end
    return nothing
end

main(ARGS)
