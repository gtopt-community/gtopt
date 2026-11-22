# -*- coding: utf-8 -*-

"""Time-axis mixin for :class:`plp2gtopt.gtopt_writer.GTOptWriter`.

Holds stage / block / phase / scenario / aperture / indhor (block-hour
map) construction.  Methods rely on ``self._normalize_method`` (provided
by the host class) and on PLP parsed data attached to ``self.parser``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict

from .aperture_writer import (
    build_aperture_array,
    build_phase_apertures,
    write_aperture_afluents,
)
from .block_writer import BlockWriter
from .index_utils import parse_index_range, parse_stages_phase
from .indhor_writer import IndhorWriter
from .stage_writer import StageWriter


class TimeMixin:
    """Stage / phase / scenario / aperture / indhor processing."""

    parser: Any
    planning: Dict[str, Any]  # see GTOptWriter.__init__

    if TYPE_CHECKING:
        # Provided by the host class (``GTOptWriter._normalize_method``,
        # a staticmethod) — reachable via MRO.  Declared for mypy only:
        # a runtime stub here would shadow the host's implementation.
        @staticmethod
        def _normalize_method(method: str) -> str: ...

    def process_stage_blocks(self, options):
        """Calculate first_block and count_block for stages, and build phase_array.

        Phase assignment priority (highest to lowest):

        1. **``stages_phase``** (explicit): A parsed ``--stages-phase`` spec
           (list-of-lists of 1-based PLP stage indices) fully controls the
           mapping regardless of ``method``.
        2. **``method='monolithic'``**: A single phase spanning all stages.
        3. **``method='sddp'``** (default): One phase per PLP stage.

        The ``--stages-phase`` option accepts a string like
        ``"1:4,5,6,7,8,9,10,..."`` (see :func:`~.index_utils.parse_stages_phase`
        for the full syntax).
        """
        stage_parser = self.parser.parsed_data.get("stage_parser", [])
        block_parser = self.parser.parsed_data.get("block_parser", [])

        stages = stage_parser.items
        for stage in stages:
            stage_blocks = [
                index
                for index, block in enumerate(block_parser.items)
                if block["stage"] == stage["number"]
            ]
            stage["first_block"] = stage_blocks[0] if stage_blocks else -1
            stage["count_block"] = len(stage_blocks) if stage_blocks else -1

        self.planning["simulation"]["block_array"] = BlockWriter(
            block_parser=block_parser, options=options
        ).to_json_array()

        self.planning["simulation"]["stage_array"] = StageWriter(
            stage_parser=stage_parser, block_parser=block_parser, options=options
        ).to_json_array(stages)

        num_stages = len(self.planning["simulation"]["stage_array"])
        stages_phase_spec = options.get("stages_phase", None)

        if stages_phase_spec:
            # Explicit stages-phase mapping: parse the spec and build phases.
            # The spec uses 1-based PLP stage indices; convert to 0-based
            # first_stage for gtopt.
            phase_groups = (
                stages_phase_spec
                if isinstance(stages_phase_spec, list)
                else parse_stages_phase(stages_phase_spec, num_stages)
            )
            phase_array = []
            for uid, group in enumerate(phase_groups, start=1):
                # group is a list of 1-based stage indices; convert to
                # 0-based and find the first and count for the contiguous run.
                first_stage_0 = group[0] - 1
                phase_array.append(
                    {
                        "uid": uid,
                        "first_stage": first_stage_0,
                        "count_stage": len(group),
                    }
                )
            self.planning["simulation"]["phase_array"] = phase_array
        else:
            method = self._normalize_method(options.get("method", "sddp"))
            if method == "monolithic":
                # One phase covering all stages
                self.planning["simulation"]["phase_array"] = [
                    {
                        "uid": 1,
                        "first_stage": 0,
                        "count_stage": num_stages,
                    }
                ]
            else:
                # SDDP: one phase per PLP stage, enabling per-stage state
                # variables (Stochastic Dual Dynamic Programming).
                self.planning["simulation"]["phase_array"] = [
                    {
                        "uid": i + 1,
                        "first_stage": i,
                        "count_stage": 1,
                    }
                    for i in range(num_stages)
                ]

    def process_scenarios(self, options):
        """Process scenario data to include block and stage information.

        **Hydrology index convention (Fortran 1-based)**

        The ``-y`` / ``hydrologies`` option uses **raw 1-based hydrology class
        indices** — the column numbers in ``plpaflce.dat``.  For example,
        ``-y 51,52`` selects hydrology classes 51 and 52 directly from the
        plpaflce flow matrix, regardless of what ``plpidsim.dat`` maps to.

        The special value ``"all"`` selects the active hydrology classes:

        * When ``plpidsim.dat`` is present: the union of all hydrology classes
          referenced by any simulation (using the stage-1 mapping), preserving
          the order in which simulations are listed in the file.
        * When ``plpidsim.dat`` is absent: all hydrology columns (1..N_hydro)
          from ``plpaflce.dat``.

        Use ``plp2gtopt --info -i <input_dir>`` to see which hydrology classes
        are active and what the idsim mapping looks like.

        **Internal representation**

        The 0-based hydrology index (``hydro_1based - 1``) is stored in each
        scenario's ``"hydrology"`` field so that
        :class:`~.aflce_writer.AflceWriter` can look up the correct column
        from the flow matrix.  The C++ solver does not use this field.

        **Per-stage hydrology rotation (plpidsim.dat)**

        PLP's simulation→hydrology mapping is **per stage**: ``SimulInd``
        advances one historical hydrological year at every January
        boundary (``plp-leeidsim.f``; consumed per stage by
        ``plp-fasedual.f:605-620``).  E.g. Sim 12 reads aflce column 62
        for stages 1-28, 63 for 29-40 and 64 for 41-52.  The full
        per-stage row of each selected simulation is therefore stored in
        the ``options["_hydrology_stage_maps"]`` side channel
        (``{scenario_uid: {plp_stage_1based: hydro_0based}}``) so
        :class:`~.aflce_writer.AflceWriter` can select each block's aflce
        column from *its* stage's idsim entry.  The scenario UID stays
        the 1-based stage-1 column (PLP convention); only the data column
        becomes stage-dependent.
        """
        # ---------------------------------------------------------------
        # Resolve the '-y' / 'hydrologies' spec to 1-based hydro indices
        # ---------------------------------------------------------------
        idsim_parser = self.parser.parsed_data.get("idsim_parser")
        hydro_spec = options.get("hydrologies", "all")
        spec = (hydro_spec or "all").strip().lower()

        # Map: stage-1 hydrology (1-based) → 0-based simulation index.
        # Used to recover each selected hydrology's full per-stage
        # SimulInd row (first-appearance simulation wins on duplicates).
        stage1_to_sim: dict[int, int] = {}
        if idsim_parser is not None and idsim_parser.num_simulations > 0:
            for sim_idx in range(idsim_parser.num_simulations):
                h1 = idsim_parser.get_index(sim_idx, 1)
                if h1 is not None and h1 not in stage1_to_sim:
                    stage1_to_sim[h1] = sim_idx

        # Helper: collect all available hydrology indices from PLP data
        def _all_hydro_indices() -> list:
            if idsim_parser is not None and idsim_parser.num_simulations > 0:
                indices: list = []
                seen_h: set = set()
                for sim_idx in range(idsim_parser.num_simulations):
                    h = idsim_parser.get_index(sim_idx, 1)
                    if h is not None and h not in seen_h:
                        seen_h.add(h)
                        indices.append(h)
                return indices
            aflce = self.parser.parsed_data.get("aflce_parser")
            if aflce and aflce.items:
                num_hydro = aflce.items[0].get("num_hydrologies", 1)
            else:
                num_hydro = 1
            return list(range(1, num_hydro + 1))

        def _num_inflow_hydrologies() -> int:
            """Number of hydrology columns (NClase) in ``plpaflce.dat``."""
            aflce = self.parser.parsed_data.get("aflce_parser")
            if aflce and aflce.items:
                return int(aflce.items[0].get("num_hydrologies", 0) or 0)
            return 0

        if spec in ("all", "first"):
            all_hydros = _all_hydro_indices()
            if spec == "first":
                hydro_indices_1based = all_hydros[:1]
            else:
                hydro_indices_1based = all_hydros
        else:
            # Explicit 1-based raw hydrology column indices (Fortran convention).
            # "-y 55,56" = hydrology classes 55 and 56 from plpaflce.dat.
            # The UID is the raw column number; when plpidsim.dat is present
            # the value selects the SIMULATION whose stage-1 column matches,
            # and that simulation's full per-stage SimulInd row drives the
            # data columns (PLP rotates one hydrological year per January).
            hydro_indices_1based = parse_index_range(hydro_spec)

            # Validate requested indices against the hydrologies present
            # in the source: every plpaflce.dat column (1..NClase) is a
            # valid choice, not only the stage-1 columns of the simulated
            # set in plpidsim.dat (e.g. 51-68 of a 68-column record).
            all_hydros = _all_hydro_indices()
            num_inflow = _num_inflow_hydrologies()
            available_set = set(all_hydros) | set(range(1, num_inflow + 1))
            invalid = sorted(set(hydro_indices_1based) - available_set)
            if invalid:
                avail_desc = (
                    f"1-{num_inflow} (plpaflce.dat columns)"
                    if num_inflow > 0
                    else ", ".join(str(h) for h in all_hydros)
                )
                raise ValueError(
                    f"Invalid hydrology indices: {invalid}. "
                    f"Available (1-based): {avail_desc}"
                )

        num_scenarios = len(hydro_indices_1based)

        # Equal probability unless explicitly overridden
        probability_factors = options.get("probability_factors", None)
        if probability_factors is None or len(probability_factors) == 0:
            probability_factors = [1.0 / num_scenarios] * num_scenarios
        else:
            probability_factors = [
                float(factor) for factor in probability_factors.split(",")
            ]

        scenarios = []
        # Per-scenario per-stage hydrology maps (PLP's rotating SimulInd
        # rows).  Kept OUT of the scenario JSON dicts — the C++ Scenario
        # contract has no such member; ``"hydrology"`` stays the stage-1
        # column for provenance.  AflceWriter consumes this side channel.
        hydrology_stage_maps: dict[int, dict[int, int]] = {}
        for hydro_1based, factor in zip(hydro_indices_1based, probability_factors):
            # Scenario UID = Fortran 1-based hydrology index (PLP convention).
            # This keeps -y values, scenario UIDs, and aperture source_scenario
            # references all using the same numbering.
            hydro_0based = hydro_1based - 1
            scenarios.append(
                {
                    "uid": hydro_1based,
                    "probability_factor": factor,
                    "hydrology": hydro_0based,
                }
            )
            # ``None`` = hydrology not the stage-1 column of any simulation
            # (handled by the rotation branch below).
            hydro_sim_idx = stage1_to_sim.get(hydro_1based)
            if hydro_sim_idx is not None and idsim_parser is not None:
                stage_row = idsim_parser.get_stage_map(hydro_sim_idx)
                if stage_row:
                    hydrology_stage_maps[hydro_1based] = {
                        stage: h - 1 for stage, h in stage_row.items()
                    }
            elif idsim_parser is not None and stage1_to_sim:
                # Hydrology present in plpaflce.dat but not the stage-1
                # column of any simulation: follow the same per-stage
                # rotation as the simulated set (one historical year per
                # January), wrapping over the NClase columns.
                shifted = self._shifted_stage_map(
                    idsim_parser,
                    min(stage1_to_sim.values()),
                    hydro_1based,
                    _num_inflow_hydrologies(),
                )
                if shifted:
                    hydrology_stage_maps[hydro_1based] = shifted
        self.planning["simulation"]["scenario_array"] = scenarios
        if options is not None:
            options["_hydrology_stage_maps"] = hydrology_stage_maps

        method = self._normalize_method(options.get("method", "sddp"))

        if method == "monolithic":
            scenes = [
                {
                    "uid": 1,
                    "first_scenario": 0,
                    "count_scenario": num_scenarios,
                }
            ]
        else:
            scenes = [
                {
                    "uid": i + 1,
                    "first_scenario": i,
                    "count_scenario": 1,
                }
                for i in range(num_scenarios)
            ]
        self.planning["simulation"]["scene_array"] = scenes

    @staticmethod
    def _shifted_stage_map(
        idsim_parser: Any, ref_sim: int, hydro_1based: int, num_hydrologies: int
    ) -> dict[int, int]:
        """Per-stage 0-based aflce columns for a non-simulated hydrology.

        Applies the stage-by-stage column offset of simulation ``ref_sim``
        (``SimulInd(ref, s) - SimulInd(ref, 1)``) to ``hydro_1based``,
        wrapping modulo ``num_hydrologies`` exactly like PLP wraps the
        last historical year back to the first (68 -> 1).  Returns an
        empty dict when the reference row or ``num_hydrologies`` is
        unavailable, which keeps the static-column behaviour.
        """
        ref_row = idsim_parser.get_stage_map(ref_sim)
        if not ref_row or num_hydrologies <= 0:
            return {}
        base = ref_row[min(ref_row)]
        return {
            stage: (hydro_1based - 1 + (h - base)) % num_hydrologies
            for stage, h in ref_row.items()
        }

    def process_apertures(self, options):
        """Build aperture_array from parsed PLP aperture index files.

        When ``plpidap2.dat`` (or ``plpidape.dat``) is present, the aperture
        definitions are converted to a gtopt ``aperture_array`` where each
        aperture references a ``source_scenario`` by UID.

        If the aperture references hydrologies that are *not* in the
        forward-scenario set, an ``aperture_directory`` is created with the
        extra affluent Parquet files, and the ``sddp_aperture_directory``
        option is set accordingly.

        Aperture configuration is fully handled through aperture_array and
        per-phase apertures in the simulation section.
        """
        if not options:
            return

        # ``-a 0`` / ``--apertures 0`` explicitly disables apertures:
        # skip all expensive aperture parsing, wetness ranking, and
        # aperture Parquet writes.  Treat integer 0 and the string "0"
        # equivalently (tests use both forms).
        num_apertures = options.get("num_apertures", "all")
        if num_apertures in (0, "0", "0-0"):
            return

        idap2_parser = self.parser.parsed_data.get("idap2_parser", None)
        idape_parser = self.parser.parsed_data.get("idape_parser", None)

        if idap2_parser is None and idape_parser is None:
            return

        # Build map: 0-based hydrology index → gtopt scenario UID
        scenarios = self.planning["simulation"].get("scenario_array", [])
        scenario_hydro_map: dict = {}
        forward_hydros: set = set()
        for scen in scenarios:
            hydro_0based = scen.get("hydrology")
            if hydro_0based is not None:
                scenario_hydro_map[hydro_0based] = scen["uid"]
                forward_hydros.add(hydro_0based)

        num_stages = len(self.planning["simulation"].get("stage_array", []))
        max_scenario_uid = max((s["uid"] for s in scenarios), default=0)

        output_dir = Path(options.get("output_dir", ""))
        aperture_dir = output_dir / "apertures"

        # Rotating plpidsim.dat (January-boundary hydrology advance): the
        # forward scenarios' Flow data is stage-rotated, but plpidap2.dat
        # aperture classes reference RAW aflce columns per stage — so no
        # aperture may alias a forward scenario.  Decouple: every aperture
        # hydro becomes a cache-backed scenario holding its raw static
        # column (see build_aperture_array(decouple_forward=...)).
        idsim_parser = self.parser.parsed_data.get("idsim_parser", None)
        rotation_active = idsim_parser is not None and idsim_parser.has_rotation(
            num_stages
        )

        result = build_aperture_array(
            idap2_parser=idap2_parser,
            scenario_hydro_map=scenario_hydro_map,
            num_stages=num_stages,
            max_scenario_uid=max_scenario_uid,
            aperture_directory=str(aperture_dir),
            aflce_parser=self.parser.parsed_data.get("aflce_parser", None),
            block_parser=self.parser.parsed_data.get("block_parser", None),
            decouple_forward=rotation_active,
        )

        if not result.aperture_array:
            return

        self.planning["simulation"]["aperture_array"] = result.aperture_array

        # Do NOT add aperture-only scenarios to scenario_array — the C++
        # solver builds full LP and output for every scenario in the array.
        # Aperture-only scenarios are served from the aperture_directory;
        # the C++ aperture solver skips source_scenarios it can't find
        # (with an info log) and uses fallback Benders cuts instead.

        # Build hydro→uid map for the extra scenarios (used by parquet writer)
        hydro_uid_map: dict[int, int] = {}
        for es in result.extra_scenarios:
            hydro_uid_map[es["hydrology"]] = es["uid"]

        # Determine which aperture hydros are NOT in the forward set.
        extra_hydros = set(hydro_uid_map.keys())

        # Write aperture-specific affluent data if needed
        if extra_hydros:
            aperture_dir.mkdir(parents=True, exist_ok=True)

            aflce_parser = self.parser.parsed_data.get("aflce_parser", None)
            central_parser = self.parser.parsed_data.get("central_parser", None)
            block_parser = self.parser.parsed_data.get("block_parser", None)

            write_aperture_afluents(
                aflce_parser=aflce_parser,
                central_parser=central_parser,
                block_parser=block_parser,
                aperture_hydros=sorted(extra_hydros),
                # Decoupled mode: forward hydros must ALSO get raw-column
                # cache files (their forward data is stage-rotated), so
                # nothing is subtracted from the aperture set.
                forward_hydros=set() if rotation_active else forward_hydros,
                output_dir=aperture_dir,
                options=options,
                hydro_uid_map=hydro_uid_map,
            )

            # Set aperture_directory in sddp_options
            sddp_opts = self.planning["options"].get("sddp_options", {})
            # Use a path relative to the JSON file location (same as
            # input_directory convention).  When the JSON is in output_dir,
            # the aperture directory is just "apertures".
            output_file = Path(options.get("output_file", ""))
            if output_file.parent == output_dir:
                sddp_opts["aperture_directory"] = "apertures"
            else:
                sddp_opts["aperture_directory"] = str(aperture_dir)
            self.planning["options"]["sddp_options"] = sddp_opts

        # NOTE: num_apertures is NOT emitted in sddp_options — the C++
        # SddpOptions JSON contract has no such field.  The aperture count is
        # fully determined by aperture_array and per-phase apertures.

        # Populate per-phase apertures from stage-indexed PLP data.
        # Pass aflce + block parsers so each phase's aperture list is
        # sorted **wettest → driest** (total flow × duration over the
        # phase's blocks).  Pairs with ``SddpOptions.num_apertures`` to
        # let each cascade level pick its top-N wettest per phase.
        phase_array = self.planning["simulation"].get("phase_array", [])
        build_phase_apertures(
            idap2_parser=idap2_parser,
            aperture_array=result.aperture_array,
            phase_array=phase_array,
            num_stages=num_stages,
            aflce_parser=self.parser.parsed_data.get("aflce_parser", None),
            block_parser=self.parser.parsed_data.get("block_parser", None),
        )

    def process_indhor(self, options):
        """Write block-to-hour map from indhor.csv if present, and record in JSON.

        When the PLP input directory contains ``indhor.csv``, plp_parser will
        have created an ``IndhorParser`` in ``parsed_data["indhor_parser"]``.
        This method writes the data to ``BlockHourMap/block_hour_map.parquet``
        and adds ``"block_hour_map"`` to the simulation section so that
        post-processing tools can reconstruct hourly time-series from
        block-level solver output.

        The ``block_hour_map`` value is the stem path (without file extension)
        relative to the output directory; gtopt resolves the actual format
        (parquet or csv) from the ``input_format`` option.
        """
        indhor_parser = self.parser.parsed_data.get("indhor_parser", None)
        if indhor_parser is None or indhor_parser.is_empty:
            return

        output_dir = Path(options["output_dir"]) if options else Path("results")
        block_hour_dir = output_dir / IndhorWriter.SUBDIR

        writer = IndhorWriter(indhor_parser, options)
        rel_path = writer.to_parquet(block_hour_dir)
        if rel_path:
            self.planning["simulation"]["block_hour_map"] = rel_path
