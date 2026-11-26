# Script mode (cmake -P): write <FILE>.build-id with the ELF GNU build-id of
# FILE, in `sha256sum`-like "<hex>  <basename>" form.  Run POST_BUILD for
# every solver plugin (cmake_local/GtoptPlugin.cmake); read by
# SolverRegistry to verify the plugin before dlopen.
#
#   cmake -DREADELF=<readelf> -DFILE=<plugin.so> -P PluginBuildIdManifest.cmake

# Script mode (-P) starts with default (OLD) policies: set them.
cmake_minimum_required(VERSION 3.25)
if(NOT READELF OR NOT FILE)
  message(FATAL_ERROR "PluginBuildIdManifest: READELF and FILE are required")
endif()
set(_out "${FILE}.build-id")
execute_process(
  COMMAND "${READELF}" -n "${FILE}"
  OUTPUT_VARIABLE _notes
  ERROR_QUIET
  RESULT_VARIABLE _rc
)
string(REGEX MATCH "Build ID:[ \t]*([0-9a-fA-F]+)" _m "${_notes}")
if(NOT _rc EQUAL 0 OR NOT CMAKE_MATCH_1)
  # No note (or no readelf output): drop a stale manifest rather than leave
  # one that would make the registry refuse a perfectly good plugin.
  file(REMOVE "${_out}")
  message(WARNING "PluginBuildIdManifest: no GNU build-id in ${FILE}")
  return()
endif()
string(TOLOWER "${CMAKE_MATCH_1}" _id)
get_filename_component(_name "${FILE}" NAME)
file(WRITE "${_out}" "${_id}  ${_name}\n")
