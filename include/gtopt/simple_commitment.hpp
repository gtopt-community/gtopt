/**
 * @file      simple_commitment.hpp
 * @brief     Deprecated SimpleCommitment input alias
 * @date      2026-04-13
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * DEPRECATED: `simple_commitment_array` is only an input alias.  Each
 * element is folded into a plain `Commitment` before the LP is built
 * (`System::fold_legacy_simple_commitments`): `dispatch_pmin` becomes
 * `Commitment.pmin`, and the generator's own `pmin` — which the legacy
 * element made conditional — moves onto the commitment.  A Commitment on
 * a non-chronological stage uses the per-block formulation the old
 * element provided (u per block, p <= Pmax*u, p >= pmin*u).
 *
 * @see Commitment, CommitmentLP
 */

#pragma once

#include <gtopt/lp_class_name.hpp>
#include <gtopt/object.hpp>

namespace gtopt
{

/**
 * @struct SimpleCommitment
 * @brief Deprecated alias of a Commitment (input only, see file docs)
 */
struct SimpleCommitment
{
  /// Legacy class name (diagnostics only; the folded element is a
  /// `Commitment`).
  static constexpr LPClassName class_name {"SimpleCommitment"};

  Uid uid {unknown_uid};  ///< Unique identifier
  Name name {};  ///< Human-readable name
  OptActive active {};  ///< Activation status (default: active)

  SingleId generator {unknown_uid};  ///< FK to the Generator

  OptTBRealFieldSched dispatch_pmin {};  ///< Min output when dispatched [MW]
  OptBool relax {};  ///< LP relaxation: u continuous in [0,1]
  OptBool must_run {};  ///< Force committed: u = 1 always
};

}  // namespace gtopt
