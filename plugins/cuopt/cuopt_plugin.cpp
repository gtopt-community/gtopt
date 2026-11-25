/**
 * @file      cuopt_plugin.cpp
 * @brief     Plugin entry point for the NVIDIA cuOpt (GPU) solver backend
 * @date      Tue Jun 10 2026
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 */

#include <cstdlib>
#include <cstring>

#include <gtopt/solver_backend.hpp>

#include "cuopt_solver_backend.hpp"

#if __has_include(<cuopt/mathematical_optimization/cuopt_c.h>)
#  include <cuopt/mathematical_optimization/cuopt_c.h>
#else
#  include <cuopt/linear_programming/cuopt_c.h>
#endif

namespace
{

// NOLINTNEXTLINE(cppcoreguidelines-avoid-c-arrays,hicpp-avoid-c-arrays,modernize-avoid-c-arrays)
const char* const k_solver_names[] = {
    "cuopt",
    nullptr,
};

}  // namespace

extern "C"
{
int gtopt_plugin_abi_version()  // NOLINT
{
  return gtopt::k_solver_abi_version;
}

const char* gtopt_plugin_name()  // NOLINT
{
  return "cuopt";
}

const char* const* gtopt_solver_names()  // NOLINT
{
  return k_solver_names;  // NOLINT
}

gtopt::SolverBackend* gtopt_create_backend(  // NOLINT
    const char* solver_name)
{
  if (std::strcmp(solver_name, "cuopt") == 0) {
    // Lazy CUDA module loading sharply cuts the per-context base VRAM tax
    // (default since CUDA 12.3; needs the env var on 12.0-12.2).  Set
    // before the first CUDA call — cuOpt initialises CUDA lazily on the
    // first solve, well after backend creation.  overwrite=0 respects a
    // user-provided value.
    // NOLINTNEXTLINE(concurrency-mt-unsafe)
    ::setenv("CUDA_MODULE_LOADING", "LAZY", 0);
    return new gtopt::CuOptSolverBackend();  // NOLINT
  }
  return nullptr;
}

double gtopt_solver_infinity(const char* solver_name)  // NOLINT
{
  if (std::strcmp(solver_name, "cuopt") == 0) {
    return gtopt::CuOptSolverBackend::plugin_infinity();
  }
  return 0.0;
}

int gtopt_solver_resource_descriptor(  // NOLINT
    const char* solver_name,
    gtopt::SolverResourceDescriptor* out)
{
  if (out == nullptr || std::strcmp(solver_name, "cuopt") != 0) {
    return 0;
  }
  *out = gtopt::SolverResourceDescriptor {
      .struct_version = gtopt::k_solver_resource_desc_version,
      .resource_class = static_cast<int>(gtopt::ResourceClass::gpu),
      .device_kind = 1,  // cuda
      // Rough estimates (refined by live NVML measurement in later phases):
      // per-context base ≈ CUDA context + cuOpt module load under lazy
      // loading; marginal ≈ FP64 CSR + PDLP iterate vectors per 1e6 nnz.
      .est_vram_mib_base = 300.0,
      .est_vram_mib_per_mnnz = 400.0,
      // Default compute mode time-slices contexts: concurrent GPU solves add
      // no throughput, only VRAM pressure — admit one solve at a time.
      .recommended_max_concurrency = 1,
      .spawns_host_threads = 1,
  };
  return gtopt::k_solver_resource_desc_version;
}

// Solver-library identity: the cuOpt headers carry no version macros, so
// the compiled version comes from the wheel's VERSION file found by
// FindCuOpt (GTOPT_CUOPT_COMPILED_VERSION_*), the runtime one from
// cuOptGetVersion() of the loaded libcuopt.
int gtopt_solver_build_info(const char* solver_name,  // NOLINT
                            gtopt::SolverBuildInfo* out)
{
  if (out == nullptr || std::strcmp(solver_name, "cuopt") != 0) {
    return 0;
  }
  cuopt_int_t major = -1;
  cuopt_int_t minor = -1;
  cuopt_int_t patch = -1;
  cuOptGetVersion(&major, &minor, &patch);
  *out = gtopt::SolverBuildInfo {
      .struct_version = gtopt::k_solver_build_info_version,
#ifdef GTOPT_CUOPT_COMPILED_VERSION_MAJOR
      .compiled_major = GTOPT_CUOPT_COMPILED_VERSION_MAJOR,
      .compiled_minor = GTOPT_CUOPT_COMPILED_VERSION_MINOR,
      .compiled_patch = GTOPT_CUOPT_COMPILED_VERSION_PATCH,
#endif
      .runtime_major = static_cast<int>(major),
      .runtime_minor = static_cast<int>(minor),
      .runtime_patch = static_cast<int>(patch),
      // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
      .library_anchor = reinterpret_cast<const void*>(&cuOptGetVersion),
  };
  return gtopt::k_solver_build_info_version;
}
}
