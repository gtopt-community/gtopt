# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Built-in parameters of ``gtopt_private_eval``, every value with a source.

Money in USD (the investment cases are in USD).  Regulator / operator
values appear only as a few cited reference parameters (link, not
embed).  ``sources`` keys are referenced in the comments next to each
value; ``[assumption]`` marks values that are this tool's assumptions,
not taken from a source.
"""

from __future__ import annotations

from typing import Any

DEFAULTS: dict[str, Any] = {
    "finance": {
        # pre-tax private WACC (real) used when neither the project nor
        # its technology sets one [assumption; cf. damodaran_crp, cne_r462]
        "wacc": 0.08,
        # fixed O&M per year as a fraction of overnight capex [cne_ictga]
        "fom_pct_capex": 0.02,
        # does the solved case already charge fixed O&M?  "auto": use the
        # element's fixed O&M field (case_fom_fields) when present, else
        # fom_pct_capex; true: annual_capcost already includes it (add
        # none); false: always fom_pct_capex
        "fom_in_case": "auto",
        # insurance in operation (all-risk property + BI + liability), %
        # of capex per year; per technology below [nrel_pv_insurance,
        # cne_peak_unit]; this default applies to unlisted technologies
        "insurance_pct_capex": 0.003,
        # true: the fixed O&M (fom_pct_capex) already includes insurance
        # (the CNE ICTGA FOM does not itemise it) -> drop the insurance line
        "insurance_in_fom": False,
        # false: drop insurance, warranty and generic guarantee lines
        "risk_costs": True,
        # extended warranty / LTSA / augmentation reserve (per technology)
        "warranty_pct_capex": 0.0,
        "warranty_usd_kw_year": 0.0,
        # generic bank guarantee / standby LC: amount (% capex) x fee for
        # guarantee_years (0 = whole life); Chile's CEN and supply-contract
        # guarantees are in markets.cl
        "guarantee_pct_capex": 0.0,
        "guarantee_fee_pct": 0.01,
        "guarantee_years": 0,
        # economic life when the element / project / technology has none
        "default_life": 25,
        # straight-line depreciation life (null = economic life)
        "depreciation_life": None,
        # capex trajectory: case capex is the base-year price; a project
        # commissioned in cod_year pays capex x I(cod)/I(base) (NREL ATB
        # 2024 index; capex_trajectory.py) [nrel_atb_2024]
        "capex_trajectory": {
            "scenario": "moderate",
            "base_year": 2026,
            "cod_year": 2026,
            "sensitivity_year": 2030,
            "scenarios": ["conservative", "moderate", "advanced"],
        },
        # fuel-cost trajectory: the declared variable cost of base_year
        # (Chile: declared to the CEN; demo cases: the case gcost) x the
        # IEA WEO 2026 fuel-price index, applied to the fuel component
        # (fuel_trajectory.py) [iea_weo_2026, cne_nt_costos_variables];
        # enabled false = flat costs.  price_scaling thermal_marginal
        # scales the CMg of periods set by a fuel-burning unit.
        "fuel_trajectory": {
            "enabled": True,
            "scenario": "steps",
            "base_year": 2026,
            "price_scaling": "thermal_marginal",
        },
    },
    "co2": {
        # carbon price in the cash flow [$/t]; 0 = not priced in the market
        "price": 0.0,
        "in_cash_flow": True,
        # NPV sensitivity to a carbon price passed through to the
        # marginal price: 5 = Chile green tax level [ley_20780]
        "sensitivity": [0.0, 5.0, 25.0, 50.0, 100.0],
        "mef_tolerance": 1e-4,
    },
    "technologies": {
        # emission_rate t/MWh [ipcc_cne_derived]; life y [cne_ictga,
        # cne_peak_unit]; capex_range $/kW low/high for the sensitivity:
        # min / max of SEIA declared amounts [seia_sample], CNE ICTGA 2026
        # [cne_ictga] and NREL ATB 2024 [nrel_atb_2024]
        "solar_pv": {
            "emission_rate": 0.0,
            "life": 25,
            "capex_range": [732, 1323],
            # replacement cost of an existing unit (--existing) [cne_ictga]
            "capex_usd_kw": 732,
            "insurance_pct_capex": 0.0025,
        },
        "wind": {
            "emission_rate": 0.0,
            "life": 25,
            "capex_range": [1380, 2326],
            "capex_usd_kw": 1433,
            "insurance_pct_capex": 0.0025,
        },
        "bess": {
            "emission_rate": 0.0,
            "life": 15,
            "capex_range": [563, 1551],
            # CNE 4 h: 722 $/kW ~ 180 $/kWh, all in the energy term
            "capex_usd_kwh": 180,
            "insurance_pct_capex": 0.004,
            # extended performance warranty / augmentation reserve [assumption;
            # cf. nrel_atb_bess 2.5 % all-in FOM, pnnl_storage_2022]
            "warranty_pct_capex": 0.005,
        },
        "ccgt": {
            "emission_rate": 0.39,
            "life": 30,
            "capex_range": [1144, 1213],
            "capex_usd_kw": 1144,
        },
        "ocgt": {
            "emission_rate": 0.56,
            "life": 25,
            "capex_range": [545, 1084],
            "capex_usd_kw": 860,
        },
        "diesel": {"emission_rate": 0.78, "life": 25, "capex_range": [576, 860]},
        "coal": {"emission_rate": 0.95, "life": 30},
        "nuclear": {"emission_rate": 0.0, "life": 40},
        "hydro": {"emission_rate": 0.0, "life": 50, "insurance_pct_capex": 0.0025},
        "geothermal": {"emission_rate": 0.0, "life": 30},
        "csp": {"emission_rate": 0.0, "life": 30},
        "thermal": {"emission_rate": 0.0},
        "line": {"emission_rate": 0.0, "life": 40, "insurance_pct_capex": 0.001},
    },
    # element ``type`` field (plp2gtopt and other converters set it) ->
    # technology; unlisted types fall back to the name hints
    "technology_types": {
        "solar": "solar_pv",
        "pv": "solar_pv",
        "solar_pv": "solar_pv",
        "csp": "csp",
        "wind": "wind",
        "hydro": "hydro",
        "hydro_ror": "hydro",
        "hydro_reservoir": "hydro",
        "renewable:hydro": "hydro",
        "geothermal": "geothermal",
        "battery": "bess",
        "nuclear": "nuclear",
        "coal": "coal",
        "ccgt": "ccgt",
        "ocgt": "ocgt",
        "diesel": "diesel",
        "thermal": "thermal",
    },
    "projects": {},
    "markets": {
        "energy_only": {"transmission": "merchant"},
        "capacity_market": {
            # PJM BRA 2027/28 clearing price 333.44 $/MW-day x 365
            # [pjm_bra_2728]; GB T-4 2029/30 was 27.10 GBP/kW-yr [gb_t4]
            "price_per_mw_year": 121706.0,
            "contract_years": None,
            "transmission": "merchant",
            # PJM 2027/28 ELCC class ratings [pjm_elcc]
            "derating": {
                "ccgt": 0.74,
                "ocgt": 0.61,
                "diesel": 0.61,
                "coal": 0.83,
                "nuclear": 0.95,
                "wind": 0.41,
                "solar_pv": 0.08,
                "hydro": 0.60,
                "default": 0.74,
            },
            "storage_derating": {"0": 0.0, "4": 0.58, "6": 0.67, "8": 0.70, "10": 0.78},
        },
        "reliability_option": {
            # Italy 2027 auction 47,000 EUR/MW-yr [it_crm_2027] (~ USD at
            # parity; set your own); strike = peaker variable cost
            # [assumption]
            "price_per_mw_year": 47000.0,
            "strike_price": 300.0,
            "contract_years": None,
            "transmission": "merchant",
            "derating": {
                "ccgt": 0.74,
                "ocgt": 0.61,
                "diesel": 0.61,
                "coal": 0.83,
                "nuclear": 0.95,
                "wind": 0.41,
                "solar_pv": 0.08,
                "hydro": 0.60,
                "default": 0.74,
            },
            "storage_derating": {"0": 0.0, "4": 0.58, "6": 0.67, "8": 0.70, "10": 0.78},
        },
        "cfd": {
            # "lcoe" = each project's own break-even strike; UK AR7 offshore
            # wind 90.91 GBP/MWh (2024 prices) [uk_ar7]; 15-year terms
            "strike": "lcoe",
            "contract_years": 15,
            "transmission": "merchant",
        },
        "cl": {
            # The CMg is the cost of the most expensive unit dispatched; no
            # scarcity rent is paid (load shedding = rationing, valued at
            # the costo de falla only for planning [cne_costo_falla_2026]).
            # "case" = highest generator variable cost of the solved case.
            "scarcity_cap": "case",
            # Precio básico de la potencia, ITD PNCP jul-2026: 9,231.51
            # CLP/kW-month at 897.64 CLP/USD [cne_itd_pncp_2026]
            "capacity_price_usd_kw_month": 10.28,
            # alternatively from the peak-unit study (mode = "peak_unit")
            "capacity_price_mode": "value",
            "peak_unit": {
                # CNE RE 803/2025, peak unit OCGT 70 MW, base jan-2024
                # [cne_peak_unit]
                "ctg_usd_kw": 649.06,
                "cse_usd_kw": 78.52,
                "clt_usd_kw": 14.08,
                "life_ctg": 25,
                "life_cse": 41,
                "life_clt": 48,
                "cfijo_usd_kw_month": 1.171,
                "cf": 1.048809,
                "rate": 0.10,
                "mrt": 0.10,
                "fp": 0.0044,
            },
            "capacity_price_factor": 1.0,
            # "case": D_peak / Σ preliminary sufficiency of the case
            "prorate": "case",
            # a unit is not paid above its preliminary sufficiency
            # [assumption]
            "prorate_cap": 1.0,
            # preliminary sufficiency / Pmax of a unit in service all year
            # [cen_psuf_2025: capacity-weighted Pini/Pmax x median
            # Psuf_pre/Pini of each CEN technology, 2025 definitive
            # calculation — coal 0.78, CCGT 0.76, OCGT 0.82, diesel
            # 0.85-0.98, hydro 0.62 x 0.97, wind 0.24, PV 0.26; all
            # thermal 0.80].  The PV / wind value is min(5-yr annual CF,
            # CF in the 52 highest load hours of the year), NOT ~0.
            "sufficiency_factors": {
                "coal": 0.78,
                "ccgt": 0.76,
                "ocgt": 0.82,
                "diesel": 0.92,
                "nuclear": 0.90,
                "hydro": 0.60,
                "wind": 0.24,
                "solar_pv": 0.26,
                "default": 0.80,
            },
            # DS 70/2023 transitory article: % of Pmax by duration (h),
            # linear in between, 0 below 1 h [ds70]
            "storage_sufficiency": {
                "1": 0.36,
                "2": 0.65,
                "3": 0.85,
                "4": 0.98,
                "5": 1.00,
            },
            "storage_availability": 1.0,
            # SSCC when the case has no reserves: MW share offered and
            # $/MW-h of reserve [cen_umc_2025a: CSF+ mean bid 2.9; share: assumption]
            "sscc": {
                "bess": {"share": 0.20, "price_usd_mw_h": 2.9},
                "ocgt": {"share": 0.10, "price_usd_mw_h": 2.9},
                "ccgt": {"share": 0.10, "price_usd_mw_h": 2.9},
                "hydro": {"share": 0.10, "price_usd_mw_h": 2.9},
                "default": {"share": 0.0, "price_usd_mw_h": 0.0},
            },
            "green_tax": {"price_usd_t": 5.0, "threshold_t_year": 25000.0},
            # transitional injection toll (Ley 20.936 art. 25 transitorio):
            # new injections are exempt (letter C) -> 0; the factor table
            # applies to pre-2016 contracts [ley_20936]
            "injection_toll_usd_mwh": 0.0,
            "cod_year": 2027,
            "toll_factors": {
                "2024": 0.6769,
                "2025": 0.5498,
                "2026": 0.5093,
                "2027": 0.4470,
                "2028": 0.3965,
                "2029": 0.3689,
                "2030": 0.3380,
                "2031": 0.1650,
                "2032": 0.1346,
                "2033": 0.1290,
                "2034": 0.0,
            },
            # overcosts + SSCC + green-tax compensations are paid pro rata
            # by withdrawals for final customers (DS 125 as amended by DS
            # 32/2025): ~3.70 USD/MWh Oct-2024..Oct-2025 [cne_sscc_reg];
            # charged on the PPA share of the output (0 = merchant)
            "withdrawal_charges_usd_mwh": 3.70,
            "ppa_withdrawal_share": 0.0,
            # CEN payment guarantee, DS 125 art. 156-157 (DS 32/2025) and
            # NT Coordinación y Operación art. 3-64..3-69 [cen_guarantee]:
            # 3 months of energy + capacity balance deficit; fee of the
            # bank guarantee per year [assumption; bank_fee_cl]
            "cen_guarantee": {
                "months": 3,
                "peak_month_factor": 1.0,
                "fee_pct": 0.01,
            },
            # supply-contract performance bond (licitación 2025/01 bases
            # §8.3): UF 600 per GWh contracted, held 51 months
            # [cne_bases_2025_01]; UF in US$ [assumption]
            "supply_contract_bond": {
                "uf_per_gwh": 600.0,
                "months": 51,
                "uf_usd": 44.0,
                "fee_pct": 0.01,
            },
            # transmission is regulated: VATT = AVI + COMA, AVI at the
            # art. 118 rate, 7 % after tax 2024–2027 [cne_r462]
            "transmission": "regulated",
            "regulated_rate": 0.07,
            # VATT = AVI + AEIR + COMA: the AEIR returns the income tax, so
            # pre-tax the line earns ~ r / (1 − 27 %) [ley_20936, sii_27]
            "regulated_tax_gross_up": 0.27,
        },
    },
    "sources": {
        "cne_itd_pncp_2026": {
            "url": "https://www.cne.cl/wp-content/uploads/2026/08/"
            "Resolucion-Exenta-397-2026.pdf",
            "date": "2026-08-03",
            "note": "ITD PNCP jul-2026: precio básico potencia 9,231.51 "
            "CLP/kW-mes at 897.64 CLP/USD",
        },
        "cne_costo_falla_2026": {
            "url": "https://www.diariooficial.interior.gob.cl/publicaciones/"
            "2026/08/21/44530/01/2857667.pdf",
            "date": "2026-08-17 (DO 2026-08-21)",
            "note": "CNE Res. Ex. 432/2026: costo de falla de larga duración"
            " SEN 0-5 % 795.11 US$/MWh; corta duración 13.73 US$/kWh",
        },
        "cen_guarantee": {
            "url": "https://www.diariooficial.interior.gob.cl/publicaciones/"
            "2026/06/17/44477/01/2825797.pdf",
            "date": "2026-06-17",
            "note": "DS 32/2025 replacing DS 125 art. 156-157: guarantee >= 3"
            " months of energy and capacity balance deficit; NT CyO art."
            " 3-64..3-69 https://www.cne.cl/wp-content/uploads/2021/08/"
            "NT-de-Coordinacion-y-Operacion-del-SEN.pdf; amounts per company:"
            " https://www.coordinador.cl/mercados/documentos/garantias/",
        },
        "cne_bases_2025_01": {
            "url": "https://www.cne.cl/wp-content/uploads/2025/04/"
            "RE-N221_Aprueba-Bases-Definitivas-2025-01.pdf",
            "date": "2025-04",
            "note": "seriedad UF 100/GWh (§4.4.6); fiel cumplimiento UF 600/GWh"
            " of the last contract year, >= 51 months (§8.3)",
        },
        "bank_fee_cl": {
            "url": "https://www.internacional.cl/files/TarifasProductos/Otros/"
            "GARANTIAS_ESTATALES_Y_OTROS_SERVICIOS_4.0.pdf",
            "date": "2026-11 (read)",
            "note": "boleta de garantía issuance fee up to 1 %, min UF 3"
            " (basis per year not stated: 1 %/yr is an assumption)",
        },
        "nrel_pv_insurance": {
            "url": "https://docs.nlr.gov/docs/fy21osti/78588.pdf",
            "date": "2021",
            "note": "NREL TP-6A20-78588 §7: PV operational insurance"
            " 0.10-0.30 % of installed cost per year",
        },
        "lbnl_wind_opex": {
            "url": "https://www.osti.gov/servlets/purl/1544993",
            "date": "2019",
            "note": "Wiser et al.: wind insurance 2.6 $/kW-yr; turbine service"
            " 14.6 $/kW-yr (Fig. 5)",
        },
        "pnnl_storage_2022": {
            "url": "https://www.energy.gov/sites/default/files/2022-09/"
            "2022%20Grid%20Energy%20Storage%20Technology%20Cost%20and%20"
            "Performance%20Assessment.pdf",
            "date": "2022",
            "note": "PNNL-33283: LFP fixed O&M 0.43 % of capex + refurbishment;"
            " NMC extended warranty ~3 $/kWh-yr",
        },
        "cne_peak_unit": {
            "url": "https://www.cne.cl/wp-content/uploads/2025/09/"
            "Rex.-CNE.N°574_ITP-Unidad-de-Punta_16-09-2025-3.pdf",
            "date": "2025-09-16 (ITD RE 803/2025, 2025-12-22)",
            "note": "peak unit: open-cycle gas turbine 70 MW, Nogales 220 kV",
        },
        "ds62_nt": {
            "url": "https://www.cne.cl/wp-content/uploads/2015/06/NT62_Ene16.pdf",
            "date": "2016-01",
            "note": "NT de Transferencias de Potencia (DS 62/2006)",
        },
        "ds42": {
            "url": "https://www.bcn.cl/leychile/navegar?idNorma=1153949",
            "date": "2020-12-26",
            "note": "DS 42/2020 amends DS 62 (balance timing, reserva estratégica)",
        },
        "cen_psuf_2025": {
            "url": "https://www.coordinador.cl/wp-content/uploads/2026/08/"
            "PSUF_def25_v2.7z",
            "date": "2026-08",
            "note": "CEN Potencia de Suficiencia 2025 definitiva v2.2: "
            "PSuficiencia_def25_v2.2.xlsx (Factores, Resultados) + report "
            "(cap. VII: PV/wind = min(5-yr CF, CF in the 52 highest "
            "hours of the annual load curve))",
        },
        "ds70": {
            "url": "https://energia.gob.cl/sites/default/files/dec_70_2023.pdf",
            "date": "2024-06-05",
            "note": "DS 70/2023 (Ley 21.505 storage): BESS sufficiency",
        },
        "ds4t_2026": {
            "url": "https://www.diariooficial.interior.gob.cl/publicaciones/"
            "2026/04/24/44433/01/2797040.pdf",
            "date": "2026-04-24",
            "note": "price decree DS 4T/2026: período de control de punta",
        },
        "cne_r462": {
            "url": "https://www.cne.cl/wp-content/uploads/2024/08/"
            "Res-462-Aprueba-Informe-Tasa-Art.-118-LGSE-VxTx-2024-2027.pdf",
            "date": "2024-08-30",
            "note": "transmission rate 7.00 % after tax 2024-2027",
        },
        "cen_sscc_2026": {
            "url": "https://www.coordinador.cl/wp-content/uploads/2025/06/"
            "2025.06.30-Informe_SSCC_2026.pdf",
            "date": "2025-06-30",
            "note": "CEN Informe SSCC 2026: auctions for CPF-/CSF/CTF",
        },
        "pexp_d37": {
            "url": "https://cartas.coordinador.cl/download_anexos/"
            "6995feb735635753b3dd4629/1",
            "date": "2026-02-13",
            "note": "Panel de Expertos D37-2025: wear costs 2-9 USD/MW-h",
        },
        "cne_sscc_reg": {
            "url": "https://www.cne.cl/wp-content/uploads/2025/05/"
            "Informe-regulatorio-SSCC.pdf",
            "date": "2025-05",
            "note": "SSCC cost ~3.70 USD/MWh (2.76 overcosts), side payments "
            "79 % of frequency-control cost (figure via press)",
        },
        "ds32_2025": {
            "url": "https://energia.gob.cl/sites/default/files/"
            "dec_32_2025publicado.pdf",
            "date": "2026-06-17",
            "note": "DS 32/2025 amends DS 125: overcost prorata on withdrawals "
            "for final customers; storage charging exempt",
        },
        "cen_umc_2025a": {
            "url": "https://www.coordinador.cl/wp-content/uploads/2025/11/"
            "INFORME-SEMESTRAL-UMC-2025a.pdf",
            "date": "2025-11",
            "note": "mean SSCC bids 1.1-2.9 USD/MW-h of reserve",
        },
        "ley_20780": {
            "url": "https://www.bcn.cl/leychile/navegar?idNorma=1067194",
            "date": "2014-09-29",
            "note": "impuesto verde 5 USD/tCO2 (art. 8; Ley 21.210 threshold)",
        },
        "ley_20936": {
            "url": "https://www.bcn.cl/leychile/navegar?idNorma=1092695",
            "date": "2016-07-20",
            "note": "transmission law: cargo único, transitional injection tolls",
        },
        "cne_ictga": {
            "url": "https://www.cne.cl/wp-content/uploads/2026/06/ICTGA-2026.pdf",
            "date": "2026-06",
            "note": "CNE technology costs: PV 732, wind 1433, BESS 4h 722, "
            "CCGT 1144, OCGT 860 USD/kW; FOM 1-2 % of capex",
        },
        "sii_27": {
            "url": "https://www.sii.cl/preguntas_frecuentes/declaracion_renta/"
            "001_140_8809.htm",
            "date": "2026-03-25",
            "note": "corporate income tax 27 % (only used to gross up the "
            "after-tax regulated transmission rate)",
        },
        "seia_sample": {
            "url": "https://seia.sea.gob.cl",
            "date": "2025-2026",
            "note": "declared investment of sample SEIA filings, see "
            "docs/methods/private-investor-evaluation-cl.md",
        },
        "iea_weo_2026": {
            "url": "https://www.iea.org/reports/world-energy-outlook-2026",
            "date": "2026-11-19",
            "note": "Table 2.2 wholesale fossil fuel prices by scenario (CPS,"
            " STEPS, ARC, NZE; 2025/2035/2050; USD 2025): Japan LNG 12.6 ->"
            " STEPS 6.8/9.9 USD/MBtu, Japan coal 117 -> 109/94 USD/t, crude 68"
            " -> 75/70 USD/bbl; CC BY 4.0",
        },
        "cne_nt_costos_variables": {
            "url": "https://www.cne.cl/wp-content/uploads/2023/06/"
            "Norma-Tecnica-de-Coordinacion-y-Operacion-Capitulo-sobre-la-"
            "Declaracion-de-Costos-Variables-2.pdf",
            "date": "2023-06 (RE CNE 259/2023)",
            "note": "art. 2-52: CV = consumo especifico neto x costo de"
            " combustible + CVNC, declared to the CEN; fuel cost and CVNC"
            " published per unit (art. 2-5)",
        },
        "cne_pncp_2025_12": {
            "url": "https://www.cne.cl/archivos_bajar/REX%20804-2025.PDF",
            "date": "2025-12-22 (REX 804)",
            "note": "cross-check: CNE fuel modulation factors 2026=1 (coal,"
            " LNG, Brent) applied to CEN-declared costs, tables 6-8",
        },
        "nrel_atb_2024": {
            "url": "https://atb.nlr.gov/electricity/2024/data",
            "date": "2024 (v4.0.0)",
            "note": "Moderate 2025: PV 1323, wind 1380, battery 4h 1551, "
            "CT 1084, NGCC 1213 USD/kW (2022 USD)",
        },
        "ipcc_cne_derived": {
            "url": "https://www.ipcc-nggip.iges.or.jp/public/2006gl/",
            "date": "2006",
            "note": "IPCC 2006 default factors x CNE heat rates [derived]",
        },
        "damodaran_crp": {
            "url": "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/"
            "datafile/ctryprem.html",
            "date": "2026-01",
            "note": "Chile country risk premium 1.10 %, ERP 5.33 %",
        },
        "pjm_bra_2728": {
            "url": "https://www.pjm.com/markets-and-operations/rpm",
            "date": "2025-07",
            "note": "BRA 2027/28 cleared at 333.44 $/MW-day (cap)",
        },
        "pjm_elcc": {
            "url": "https://www.pjm.com/planning/resource-adequacy-planning/"
            "effective-load-carrying-capability",
            "date": "2025-07",
            "note": "2027/28 ELCC class ratings",
        },
        "gb_t4": {
            "url": "https://www.emrdeliverybody.com/",
            "date": "2026-03",
            "note": "T-4 2029/30 cleared at 27.10 GBP/kW-yr",
        },
        "it_crm_2027": {
            "url": "https://www.terna.it/en/electric-system/capacity-market",
            "date": "2025",
            "note": "Italy CM 2027 auction 47,000 EUR/MW-yr (reliability option)",
        },
        "uk_ar7": {
            "url": "https://www.gov.uk/government/publications/"
            "contracts-for-difference-cfd-allocation-round-7-results",
            "date": "2026-01",
            "note": "AR7 offshore wind 90.91 GBP/MWh (2024 prices)",
        },
    },
}
