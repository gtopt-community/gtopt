# Script mode (cmake -P): writes ${OUTPUT} with the git revision of
# ${SOURCE_DIR}.  Run on every build by the `gtopt_git_revision` target;
# configure_file only rewrites the header when the revision changed, so
# an unchanged tree triggers no recompilation.
# A source tree without its own `.git` (e.g. an unpacked release tarball)
# stays "unknown" even when it sits inside some other git checkout.
set(GTOPT_GIT_REVISION "unknown")
find_package(Git QUIET)
if(GIT_FOUND AND EXISTS "${SOURCE_DIR}/.git")
  execute_process(
    COMMAND "${GIT_EXECUTABLE}" describe --always --dirty --abbrev=12
    WORKING_DIRECTORY "${SOURCE_DIR}"
    OUTPUT_VARIABLE _rev
    OUTPUT_STRIP_TRAILING_WHITESPACE
    ERROR_QUIET
    RESULT_VARIABLE _rc
  )
  if(_rc EQUAL 0 AND NOT _rev STREQUAL "")
    set(GTOPT_GIT_REVISION "${_rev}")
  endif()
endif()
configure_file("${TEMPLATE}" "${OUTPUT}" @ONLY)
