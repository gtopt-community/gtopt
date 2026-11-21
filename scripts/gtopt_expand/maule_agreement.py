# -*- coding: utf-8 -*-

"""Maule irrigation agreement → gtopt entities.

Reads a canonical ``maule.json`` agreement description (the Stage-1 product
of ``plp2gtopt`` or hand-authored) and produces:

* ``FlowRight`` entries (electric, irrigation, mixed, La Invernada
  branches, Resolution 105 ecological flow, district withdrawals).
* ``VolumeRight`` entries (rights buckets + economy trackers + La
  Invernada economy).
* ``UserConstraint`` entries (zone partition balances, district shares).
* A companion ``maule.pampl`` file containing the named sets and
  parameters.

Machicura-model variants
------------------------

The agreement supports two topology variants for Central Machicura.
Selection follows a three-level priority order so every downstream
consumer — tests, hand-authored JSON, and the full pipeline — gets a
deterministic answer without a CLI flag:

1. **Explicit override** — if ``cfg["machicura_model"]`` is set (either
   to ``"pasada"``/``"run-of-river"`` or ``"embalse"``/``"reservoir"``),
   that value wins.  Used by hand-authored fixtures and tests that want
   to pin the variant regardless of the surrounding gtopt case.

2. **Reservoir auto-detection** — otherwise, when
   ``options["reservoir_names"]`` contains the resolved
   ``junction_retiro`` (by default ``"MACHICURA"``, or the
   ``downstream`` field of the PLP COLBUN extraction entry when
   present in ``cfg["extrac_entries"]``), the ``embalse`` variant is
   selected.  This lets ``gtopt_expand`` detect a MACHICURA
   reservoir in the actual gtopt case — e.g. the planning JSON that
   plp2gtopt emits next to ``maule.json`` when ``--ror-as-reservoirs``
   promoted MACHICURA to a daily-cycle reservoir.  The CLI populates
   ``reservoir_names`` automatically from a sibling planning file.

3. **Default** — otherwise ``pasada`` (PLP-compatible, no downstream
   reservoir).  Safe fallback for cases that carry no planning JSON
   (synthetic fixtures, constraint-only tests, documentation snippets).

The two variants are:

* ``"pasada"`` (English synonym ``"run-of-river"``): historical
  PLP-compatible variant.  Machicura is a pass-through junction
  (pasada semantics, no volumetric balance of its own) and the
  retiros of riego and Res 105 are implicitly anchored at the Colbun
  reservoir.  Uses ``templates/maule.tson`` and ``templates/maule.tampl``.

* ``"embalse"`` (English synonym ``"reservoir"``): full physical variant.
  Machicura is modelled as a daily-cycle reservoir aguas abajo of Colbun
  and the three riego/Res 105 FlowRights carry an explicit ``junction``
  reference to it.  Uses ``templates/maule_machicura.tson`` and
  ``templates/maule_machicura.tampl``.

The ``embalse`` variant additionally requires:

* ``junction_retiro``: name of the downstream junction (auto-resolved from
  ``extrac_entries`` if present — matches the ``downstream`` field of the
  PLP COLBUN extraction entry — or defaults to ``"MACHICURA"``).
* ``machicura_vmax``: useful regulation volume [hm³] (default 12.0).
* ``machicura_capacity``: installed capacity [MW] (default 95.0).
* ``machicura_qmax``: turbine max flow [m³/s] (default 280.0).
* ``machicura_prod_factor``: production factor [MW/(m³/s)] (default 0.327).

This is the canonical Stage-2 transform in the irrigation pipeline (see
``project_irrigation_pipeline.md``).  Stage 1 (``plp2gtopt``) converts
``plpmaulen.dat`` to ``maule.json``; Stage 3 (``gtopt``) consumes the
entities the same way it consumes any other JSON/PAMPL input.

``maule.json`` schema
---------------------

A flat dict mirroring the field set produced by ``MauleParser`` (see
``scripts/plp2gtopt/maule_parser.py``).  Required keys include
``central_colbun``, ``central_invernada``, the three-zone volume
thresholds (``v_reserva_extraord``, ``v_reserva_ordinaria``), monthly
modulation arrays (``mod_elec_reserva``, ``pct_riego_mensual``,
``caudal_res105``), penalty/cost fields (``costo_embalsar``,
``costo_no_embalsar``, ``costo_canelon``, ``penalizador_1``,
``costo_riego_ns_*``), the initial bucket levels (``v_*_ini``) and the
``districts`` list.  See ``MauleParser`` for the authoritative definition.
"""

from __future__ import annotations

import logging
from typing import Any

from gtopt_expand._base import _RightsAgreementBase
from gtopt_expand._base import hydro_month_name as _hydro_month_name

_logger = logging.getLogger(__name__)

# PLP HorasMesHid (plpmod.f:24) by calendar month, and FactTiempoH.
_HOURS_PER_MONTH = {
    1: 744.0,
    2: 672.0,
    3: 744.0,
    4: 720.0,
    5: 744.0,
    6: 720.0,
    7: 744.0,
    8: 744.0,
    9: 720.0,
    10: 744.0,
    11: 720.0,
    12: 744.0,
}
_HM3_PER_M3S_H = 0.0036


class MauleAgreement(_RightsAgreementBase):
    """Stage-2 transform: maule.json → FlowRight/VolumeRight/UserConstraint.

    The JSON entity structure is defined in ``templates/maule.tson``.
    This class pre-computes all dynamic values (schedules, costs, zone
    thresholds) and passes them as template context parameters.

    Args:
        maule_config: Canonical Maule configuration dict (see module
            docstring for the schema).
        stage_parser: Optional stage-parser-shaped object whose ``get_all()``
            yields per-stage dicts with ``number`` and ``month``.  When
            omitted, monthly schedules stay in raw calendar form.
        options: Conversion options dict (e.g. ``last_stage``,
            ``blocks_per_stage``).
    """

    _ARTIFACT = "maule"
    _UID_START = 1000  # avoid collisions with Laja (2000..)

    #: supported ``machicura_model`` values → template stem
    _VARIANT_TEMPLATES: dict[str, str] = {
        "pasada": "maule",
        "embalse": "maule_machicura",
    }

    #: English synonyms accepted for ``machicura_model`` — normalized to the
    #: canonical Spanish keys of ``_VARIANT_TEMPLATES``.
    _VARIANT_SYNONYMS: dict[str, str] = {
        "pasada": "pasada",
        "run-of-river": "pasada",
        "run_of_river": "pasada",
        "embalse": "embalse",
        "reservoir": "embalse",
    }

    def __init__(
        self,
        maule_config: dict[str, Any],
        stage_parser: Any = None,
        options: dict[str, Any] | None = None,
        water_value_resolver: Any = None,
    ):
        super().__init__(
            maule_config,
            stage_parser=stage_parser,
            options=options,
            water_value_resolver=water_value_resolver,
        )

    # ----------------------------------------------------------- variant
    def _machicura_model(self) -> str:
        """Return the active Machicura topology variant.

        Resolution order (first match wins):

        1. ``cfg["machicura_model"]`` — explicit per-case override,
           accepted as either the canonical Spanish keys
           (``pasada``/``embalse``) or the English synonyms
           (``run-of-river``/``reservoir``) via ``_VARIANT_SYNONYMS``.
           Used mainly by hand-authored fixtures and tests.

        2. ``options["reservoir_names"]`` — set/iterable of reservoir
           names coming from the companion gtopt/planning case.  When
           the resolved ``junction_retiro`` (``_resolve_junction_retiro``)
           appears in that set, MACHICURA is physically a daily-cycle
           reservoir in the case, so the ``embalse`` variant is
           selected automatically.  This is how the CLI detects the
           MACHICURA promotion performed by
           ``plp2gtopt --ror-as-reservoirs`` without any explicit flag.

        3. Default ``pasada`` — safe fallback when no information is
           available (synthetic fixtures, constraint-only tests).
        """
        raw = self._cfg.get("machicura_model")
        if raw is not None:
            key = str(raw).strip().lower()
            if key not in self._VARIANT_SYNONYMS:
                raise ValueError(
                    f"unknown machicura_model {raw!r}: "
                    f"expected one of {sorted(self._VARIANT_SYNONYMS)}"
                )
            return self._VARIANT_SYNONYMS[key]

        reservoir_names = self._options.get("reservoir_names")
        if reservoir_names:
            junction_retiro = self._resolve_junction_retiro()
            # Case-insensitive membership check so callers can supply
            # names straight from the planning JSON without worrying
            # about the Spanish/English mixed casing used by PLP.
            lowered = {str(n).strip().lower() for n in reservoir_names}
            if junction_retiro.strip().lower() in lowered:
                return "embalse"

        return "pasada"

    def _template_name(self) -> str:  # noqa: D401
        return self._VARIANT_TEMPLATES[self._machicura_model()]

    def _resolve_junction_retiro(self) -> str:
        """Resolve the downstream junction name for the machicura variant.

        Looks up ``cfg["extrac_entries"]`` (Stage-1 output of
        ``plpextrac.dat``) for a COLBUN entry and returns its ``downstream``
        field.  Falls back to ``cfg["junction_retiro"]`` or ``"MACHICURA"``
        so hand-authored fixtures can override the PLP default.
        """
        cfg = self._cfg
        override = cfg.get("junction_retiro")
        if override:
            return str(override)

        extrac_entries = cfg.get("extrac_entries") or []
        central_colbun = str(cfg.get("central_colbun", "")).upper()
        for entry in extrac_entries:
            name = str(entry.get("name", "")).upper()
            if name in (central_colbun, "COLBUN"):
                downstream = entry.get("downstream")
                if downstream:
                    return str(downstream)

        return "MACHICURA"

    def _monthly_fmax_schedule(
        self,
        monthly_pcts: list[float],
        base_flow: float,
    ) -> float | list[list[float]]:
        """Build per-stage-block fmax from monthly percentage × base flow.

        Returns a scalar if all stages have the same value, otherwise a
        2D ``[[v]*nblocks for v in values]`` list compatible with
        ``OptTBRealFieldSched`` (per-stage-block).  `FlowRight.fmax` is
        a 2D field (re-widened from 1D in the 2026-05 type-widening
        agent so per-stage-block parquet round-trips no longer warn).
        """
        monthly_flows = [pct / 100.0 * base_flow for pct in monthly_pcts]
        schedule = self._hydro_to_stage_schedule(monthly_flows)
        return self._to_tb_sched(schedule)

    def _stage_monthly_values(
        self,
        default_hydro: list[float],
        manual_key: str,
        n_manual_key: str,
    ) -> list[float]:
        """Per-stage values of a hydro-month array with PLP manual years.

        PLP keeps a ``(month, year)`` table initialised from the default
        12-value array and overwritten, for the first ``NYear`` years, by
        the manual rows of ``plpmaulen.dat`` (``PRiegoMauleAnual`` /
        ``QRiego105Anual``, genpdmaule.f:1670-1736); stages look the
        table up by their ``plpeta.dat`` year.  ``manual_key`` holds
        either one 12-value row (legacy canonical JSON) or a list of
        rows (one per manual year).  Without stage metadata the raw
        hydro-month array is returned.
        """
        cfg = self._cfg
        raw_manual = cfg.get(manual_key) or []
        if raw_manual and not isinstance(raw_manual[0], (list, tuple)):
            raw_manual = [raw_manual]
        n_manual = min(int(cfg.get(n_manual_key, 0) or 0), len(raw_manual))
        if self._stage_parser is None:
            return [float(v) for v in default_hydro]
        values: list[float] = []
        for stage in self._get_stages():
            hydro_idx = (int(stage.get("month", 1)) - 4) % 12
            year = int(stage.get("year", 0) or 0)
            row = raw_manual[year - 1] if 1 <= year <= n_manual else default_hydro
            values.append(float(row[hydro_idx]))
        return values

    def _pct_riego_stage(self) -> list[float]:
        """Per-stage irrigation percentage [%] (PRiegoMauleAnual)."""
        return self._stage_monthly_values(
            [float(v) for v in self._cfg["pct_riego_mensual"]],
            "pct_riego_manual",
            "n_agnos_pct_manual",
        )

    def _ledger_context(self) -> dict[str, Any]:
        """Depletion-ledger values of the Maule rights counters.

        PLP keeps the electric/irrigation spending as UP-counters
        (``IVMGEMF/IVMGEAF/IVMGRTF = prev + dur·q`` capped by
        ``IVMDEMT/IVMDEAT/IVMDRTT``, genpdmaule.f:269-351) and the
        ``plpmaulen.dat`` initial values are amounts already USED.
        gtopt models every counter as a REMAINING-rights depletion
        ledger (``remaining = cap − used``): the balance row then reads
        ``remaining_t = remaining_{t-1} − dur·q`` with ``remaining ≥ 0``,
        whose dual (the value of one more hm³ of right) is ≥ 0.  An
        up-counter ``used ≤ cap`` carries a ≤ 0 dual that the SDDP state
        propagates as a NEGATIVE water value — the reason this form is
        used.  Never pass the raw "used" value as ``eini``.

        * monthly electric cap ``VDerElecMenMax = GastoElecMenMax ×
          HorasMesHid × 0.0036`` [hm³] (genpdmaule.f:1643-1646) — a
          per-stage ``emax``; ``GastoElecMenMax`` is a RATE [m³/s].
        * ordinary-reserve rights (``IVMDOEF/IVMDORF`` minus
          ``IVMGOEF/IVMGORF``): FijaMaule provisions ``PorcQNIDE/R ·
          (V_util − VReservaExtraord)`` when entering the ordinary
          regime with no rights (genpdmaule.f:802-810) and at every
          January in that regime (:977-987); rights then accrue with
          ``PorcQNIDE/R · QAflMaule`` (rows :300-307).
        """
        cfg = self._cfg
        stages = self._get_stages() if self._stage_parser is not None else []
        rate = float(cfg["gasto_elec_men_max"])
        if stages:
            men_caps = [
                rate * _HOURS_PER_MONTH[int(st.get("month", 1))] * _HM3_PER_M3S_H
                for st in stages
            ]
        else:
            men_caps = [rate * 744.0 * _HM3_PER_M3S_H]
        men_ini = max(men_caps[0] - float(cfg["v_gasto_elec_men_ini"]), 0.0)
        anu_ini = max(
            float(cfg["v_der_elect_anu_max"]) - float(cfg["v_gasto_elec_anu_ini"]),
            0.0,
        )
        riego_ini = max(
            float(cfg["v_der_riego_temp_max"]) - float(cfg["v_gasto_riego_ini"]),
            0.0,
        )

        v_extraord = float(cfg["v_reserva_extraord"])
        v_normal = v_extraord + float(cfg["v_reserva_ordinaria"])
        v_util_min = float(cfg.get("v_util_min", 0.0))
        p_elec = float(cfg["pct_elec_reserva"]) / 100.0
        p_riego = float(cfg["pct_riego_reserva"]) / 100.0
        der_e = float(cfg.get("v_der_rext_elec_ini", 0.0) or 0.0)
        der_r = float(cfg.get("v_der_rext_riego_ini", 0.0) or 0.0)
        used_e = float(cfg.get("v_gasto_rext_elec_ini", 0.0) or 0.0)
        used_r = float(cfg.get("v_gasto_rext_riego_ini", 0.0) or 0.0)
        v_ini = cfg.get("v_ini_maule")
        if v_ini is not None:
            v_util = float(v_ini) - v_util_min
            if v_util >= v_normal:
                # Normal regime: PLP zeroes the ordinary-reserve ledgers.
                der_e = der_r = used_e = used_r = 0.0
            elif der_e <= 0.0 and der_r <= 0.0:
                reserva = max(v_util - v_extraord, 0.0)
                der_e, der_r = p_elec * reserva, p_riego * reserva
        res_ini_e = max(der_e - used_e, 0.0)
        res_ini_r = max(der_r - used_r, 0.0)
        # January provisioning rule on the absolute Maule volume V:
        # p · (V − v_util_min − VReservaExtraord), 0 below the reserve.
        v_base = v_util_min + v_extraord
        cfg["ledger_res_ini_elec"] = res_ini_e
        cfg["ledger_res_ini_riego"] = res_ini_r
        return {
            "men_emax": self._to_tb_sched(men_caps),
            "men_ini": men_ini,
            "anu_ini": anu_ini,
            "riego_ini": riego_ini,
            "res_ini_elec": res_ini_e,
            "res_ini_riego": res_ini_r,
            "res_emax_elec": p_elec * float(cfg["v_reserva_ordinaria"]),
            "res_emax_riego": p_riego * float(cfg["v_reserva_ordinaria"]),
            "res_rule_v_base": v_base,
            "res_rule_v_normal": v_util_min + v_normal,
            "p_elec": p_elec,
            "p_riego": p_riego,
        }

    def _cota_425_context(self) -> dict[str, float] | None:
        """PLP Cota 425 rule values, or ``None`` when not applicable.

        Needs the resolved extraction waterway (``extraction_ref_425``,
        injected by plp2gtopt from ``IExtr425``) and ``Vol425 > 0``.
        """
        cfg = self._cfg
        volume = float(cfg.get("v_cota_425", 0.0) or 0.0)
        if volume <= 0.0:
            return None
        if not cfg.get("extraction_ref_425"):
            _logger.warning(
                "maule: Cota 425 rule (IExtr425=%s, Vol425=%g hm3) has no"
                " resolved COLBUN extraction waterway — the rule is NOT"
                " applied.",
                cfg.get("idx_extrac_colbun_425"),
                volume,
            )
            return None
        return {"volume": volume, "qmax": float(cfg.get("qmax_425", 0.0) or 0.0)}

    def _qdef_riego_stage(self) -> list[float] | None:
        """Per-stage PLP ``QDefRiego = max(friego·GastoRiegoMax − QHoyaInter, 0)``.

        GetQsMaule (genpdmaule.f:640-656).  ``None`` without stage
        metadata or without the hoya-intermedia series.
        """
        cfg = self._cfg
        q_hoya = cfg.get("q_hoya_inter_maule")
        if self._stage_parser is None or q_hoya is None:
            return None
        pct = self._pct_riego_stage()
        gmax = float(cfg["gasto_riego_max"])
        return [
            max(
                pct[i] / 100.0 * gmax - (float(q_hoya[i]) if i < len(q_hoya) else 0.0),
                0.0,
            )
            for i in range(len(pct))
        ]

    def _invernada_context(self) -> dict[str, Any]:
        """Template values of the La Invernada level rule.

        PLP (FijaMaule, genpdmaule.f:861-911; GetQsMaule :640-656)
        classifies each stage: ``FInvernDesemb`` (deficit — no storing
        allowed) when ``QDefRiego = max(QRiego − QHoyaInter, 0)`` exceeds
        the Invernada filtration, or when ``QHoyaInter + QAflInvern`` is
        below ``friego · GastoMauleMin``.  Deficit stages enable
        ``qidn`` and close ``qisd``; the others do the opposite.  The
        hard IQNINV / IQRNIN rows are emitted only for the deficit
        stages and only when ``RelaxInvern`` is false (with it the PLP
        row is deactivated, see the template).

        The flow terms of the classification are the per-stage means of
        the hydrologies (``q_hoya_inter_maule`` / ``q_afl_invernada``,
        injected by plp2gtopt) and ``filtracion_invernada`` [m³/s] — the
        same scenario-invariant approximation the Laja ``qdefm`` carrier
        uses (gtopt FlowRight bounds have no scenario axis).
        """
        cfg = self._cfg
        qmax = float(cfg.get("qmax_invernada", 200.0))
        relax = bool(cfg.get("relax_invernada", cfg.get("flag_embalsa_1", True)))
        level_rule = (not relax) and bool(cfg.get("inflow_ref_invernada"))
        if not relax and not level_rule:
            _logger.warning(
                "maule: RelaxInvern = F but no natural-inflow Flow element"
                " was resolved for central %r — the La Invernada level rule"
                " (PLP IQNINV/IQRNIN) is NOT emitted.",
                cfg.get("central_invernada"),
            )
        if level_rule and bool(cfg.get("flag_embalsa_2", False)):
            _logger.warning(
                "maule: NoDesembInv = T zeroes the qhein coefficient of"
                " PLP IQRNIN only while the ordinary reserve is active"
                " (a state-dependent switch); gtopt keeps the qhein term"
                " (PLP default coefficient 1) in every deficit stage."
            )
        if not level_rule or self._stage_parser is None:
            self._cfg["invernada_level_rule"] = level_rule
            self._cfg.pop("invernada_deficit_stages", None)
            return {
                "qmax": qmax,
                "level_rule": level_rule,
                "fmax_deficit": qmax,
                "fmax_sin_deficit": qmax,
            }

        pct = self._pct_riego_stage()
        q_hoya = cfg.get("q_hoya_inter_maule") or []
        q_afl = cfg.get("q_afl_invernada") or []
        qfilt = float(cfg.get("filtracion_invernada", 0.0))
        gmax = float(cfg["gasto_riego_max"])
        gmin = float(cfg.get("caudal_min_embalsa", 0.0))
        deficit_stages: list[int] = []
        flags: list[bool] = []
        for i, stage in enumerate(self._get_stages()):
            friego = pct[i] / 100.0
            qh = float(q_hoya[i]) if i < len(q_hoya) else 0.0
            qa = float(q_afl[i]) if i < len(q_afl) else 0.0
            desemb = max(friego * gmax - qh, 0.0) > qfilt
            if not desemb:
                desemb = (qh + qa) < friego * gmin
            flags.append(desemb)
            if desemb:
                deficit_stages.append(int(stage.get("number", i + 1)))
        self._cfg["invernada_level_rule"] = bool(deficit_stages)
        self._cfg["invernada_deficit_stages"] = deficit_stages
        return {
            "qmax": qmax,
            "level_rule": bool(deficit_stages),
            "fmax_deficit": self._to_tb_sched([qmax if f else 0.0 for f in flags]),
            "fmax_sin_deficit": self._to_tb_sched([0.0 if f else qmax for f in flags]),
        }

    def _prepare_context(self) -> dict[str, Any]:
        """Prepare the template context with all pre-computed values.

        Returns a dict of parameters that the maule.tson template uses
        via @param@ substitution.
        """
        cfg = self._cfg

        res_colbun = cfg["central_colbun"]
        # Zone driver: the convenio's reserve zones are LAGUNA DEL
        # MAULE volumes, not Colbun — PLP's IVMUTIL row is
        # `vol(IEmbMaule) - vmutil = VolUtilMin` (genpdmaule.f:326-329)
        # and FijaMaule's RegimenNormal check reads that state.  (The
        # 1947 convenio predates Colbun by four decades; Colbun's
        # volume only drives the separate cota-425 extraction rule.)
        res_maule = cfg["central_maule"]

        # Riego season start (PLP MesRiegoIni, genpdmaule.f:1887-1895):
        # first hydro month where the irrigation percentage turns
        # non-zero after a zero month.  The seasonal riego bucket
        # (IVMGRTF) restarts there — TipoEtaDR == INICIOANO.
        pct_hydro = [float(v) for v in cfg["pct_riego_mensual"]]
        riego_ini_hydro = 1
        for i in range(1, 12):
            if pct_hydro[i - 1] == 0.0 and pct_hydro[i] != 0.0:
                riego_ini_hydro = i + 1
                break
        reset_month_riego = _hydro_month_name(riego_ini_hydro)
        v_extraord = cfg["v_reserva_extraord"]
        v_ordinaria = cfg["v_reserva_ordinaria"]
        v_zone_extraord = v_extraord
        v_zone_normal = v_extraord + v_ordinaria

        elec_day_max = cfg["gasto_elec_dia_max"]
        riego_max = cfg["gasto_riego_max"]

        # --- Schedules ---
        pct_riego = cfg["pct_riego_mensual"]
        if self._stage_parser is not None:
            # Manual years (PRiegoMauleAnual) keyed by the plpeta year.
            irr_fmax_schedule = self._to_tb_sched(
                [p / 100.0 * float(riego_max) for p in self._pct_riego_stage()]
            )
        else:
            irr_fmax_schedule = self._monthly_fmax_schedule(pct_riego, riego_max)

        mod_elec = cfg["mod_elec_reserva"]
        elec_ord_fmax = self._monthly_fmax_schedule(mod_elec, elec_day_max)

        caudal_res105 = cfg["caudal_res105"]
        if self._stage_parser is not None:
            # Manual years (QRiego105Anual) keyed by the plpeta year.
            res105_values = self._stage_monthly_values(
                [float(v) for v in caudal_res105],
                "caudal_res105_manual",
                "n_agnos_res105_manual",
            )
        else:
            res105_values = self._hydro_to_stage_schedule(caudal_res105)
        # `discharge` aliases `target` (OptTBRealFieldSched / 2D) —
        # emit scalar or 2D so the C++ JSON parser variant matches.
        res105_discharge = self._to_tb_sched(res105_values)

        valor_riego = cfg.get("valor_riego_maule", 0)
        valor_riego_res105 = cfg.get("valor_riego_res105", 0)

        costo_embalsar = cfg.get("costo_embalsar", 1500.0)
        costo_no_embalsar = cfg.get("costo_no_embalsar", 1000.0)
        costo_canelon = cfg.get("costo_canelon", 0.0)

        # --- Rights ledgers (remaining = cap - used) ---
        ledgers = self._ledger_context()

        # --- La Invernada level rule (PLP IQNINV / IQRNIN) ---
        invernada = self._invernada_context()

        # --- District entities (pre-computed) ---
        district_flow_rights, district_constraints = self._compute_district_entities(
            float(riego_max)
        )
        qdef = self._qdef_riego_stage()
        self._cfg["maule_qdef_riego"] = qdef is not None
        canelon_anchored = bool(cfg.get("anchor_gen_ref_canelon"))
        if float(cfg.get("costo_canelon", 0.0) or 0.0) > 0 and not canelon_anchored:
            _logger.warning(
                "maule: bocatoma %r has no generation waterway — its"
                " CostoCanelon is dropped (an unanchored priced FlowRight"
                " would be a phantom).",
                cfg.get("bocatoma_canelon"),
            )

        # --- Machicura variant parameters ---
        # These are always computed (cheap) so tests and debug dumps can
        # inspect them; the machicura template uses `junction_retiro`
        # unconditionally, the plp template ignores them.
        junction_retiro = self._resolve_junction_retiro()
        machicura_vmax = cfg.get("machicura_vmax", 12.0)
        machicura_capacity = cfg.get("machicura_capacity", 95.0)
        machicura_qmax = cfg.get("machicura_qmax", 280.0)
        machicura_prod_factor = cfg.get("machicura_prod_factor", 0.327)

        return {
            # Reservoir / zone thresholds
            "res_colbun": res_colbun,
            "res_maule": res_maule,
            "reset_month_riego": reset_month_riego,
            "v_zone_extraord": v_zone_extraord,
            "v_zone_normal": v_zone_normal,
            "v_reserva_extraord": v_extraord,
            # Flow limits
            "elec_day_max": elec_day_max,
            "riego_max": riego_max,
            # Schedules
            "irr_fmax_schedule": irr_fmax_schedule,
            "elec_ord_fmax": elec_ord_fmax,
            "res105_discharge": res105_discharge,
            "qdrah_fmax": 2.0 * float(riego_max),
            "qdef_riego": None if qdef is None else self._to_tb_sched(qdef),
            "canelon_anchored": canelon_anchored,
            "cota_425": self._cota_425_context(),
            # Costs / penalties
            "penalizador_1": cfg.get("penalizador_1", 1500),
            "costo_riego_ns_maule": cfg.get("costo_riego_ns_maule", 1000),
            "valor_riego": valor_riego,
            "costo_riego_ns_res105": cfg.get("costo_riego_ns_res105", 1000),
            "valor_riego_res105": valor_riego_res105,
            "costo_embalsar": costo_embalsar,
            "costo_no_embalsar": costo_no_embalsar,
            "costo_canelon": costo_canelon,
            # VolumeRight initial values and caps
            "ledger": ledgers,
            "v_der_elect_anu_max": cfg["v_der_elect_anu_max"],
            "v_der_riego_temp_max": cfg["v_der_riego_temp_max"],
            "v_comp_elec_ini": cfg["v_comp_elec_ini"],
            "v_comp_elec_max": cfg["v_comp_elec_max"],
            # La Invernada
            "central_invernada": cfg["central_invernada"],
            "v_econ_inver_ini": cfg["v_econ_inver_ini"],
            "qmax_invernada": invernada["qmax"],
            "invernada_level_rule": invernada["level_rule"],
            "fmax_invernada_deficit": invernada["fmax_deficit"],
            "fmax_invernada_sin_deficit": invernada["fmax_sin_deficit"],
            # District entities
            "district_flow_rights": district_flow_rights,
            "district_constraints": district_constraints,
            # Machicura variant parameters (see class docstring)
            "junction_retiro": junction_retiro,
            "machicura_vmax": machicura_vmax,
            "machicura_capacity": machicura_capacity,
            "machicura_qmax": machicura_qmax,
            "machicura_prod_factor": machicura_prod_factor,
        }

    def _compute_district_entities(
        self,
        district_fmax: float,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Pre-compute district FlowRight and UserConstraint entities.

        Returns:
            Tuple of (district_flow_rights, district_constraints).
        """
        cfg = self._cfg
        district_flow_rights: list[dict[str, Any]] = []
        district_constraints: list[dict[str, Any]] = []

        cost_ns = float(cfg.get("costo_riego_ns_res105", 1000.0))
        for district in cfg["districts"]:
            pct = district["percentage"]

            # Transform district name: Rie prefix → retiro_ prefix
            raw_name = district["name"]
            if raw_name.startswith("Rie"):
                fr_name = "retiro_" + raw_name[3:]
            else:
                fr_name = raw_name

            fr_district: dict[str, Any] = {
                "name": fr_name,
                "purpose": "irrigation",
                "direction": -1,
                # PLP qri has no upper bound (GenPDMauleEtaFO); the
                # dist_* row ties it to qa105 <= QRiego105.
                "fmax": district_fmax,
            }
            anchor_junction = district.get("anchor_junction")
            if anchor_junction and cfg.get("enable_physical_anchoring", True):
                # Pure irrigation withdrawal — the retiro takes real
                # water at the district's own junction (no diversion
                # central; complements the dist_anclaje_* path).
                fr_district["junction_a"] = anchor_junction
            district_flow_rights.append(fr_district)

            # PLP "entregas de riego" (genpdmaule.f:431-440):
            #   qri + qrhh = PRet · qa105     (qrhh only with slack,
            #   priced CostoRiegoNS105)
            lhs = f"flow_right('{fr_name}').flow"
            if district["has_slack"]:
                deficit_name = "deficit_" + fr_name.removeprefix("retiro_")
                district_flow_rights.append(
                    {
                        "name": deficit_name,
                        "purpose": "irrigation",
                        "direction": -1,
                        "target": 0,
                        "fmax": district_fmax,
                        "uvalue": -cost_ns,
                    }
                )
                lhs += f" + flow_right('{deficit_name}').flow"
            district_constraints.append(
                {
                    "name": f"dist_{fr_name}",
                    "expression": (
                        f"{lhs} = {pct / 100.0} * "
                        "flow_right('maule_resolucion_105').flow"
                    ),
                    "description": f"{fr_name}: {pct}% of Res. 105 delivery",
                }
            )

        return district_flow_rights, district_constraints
