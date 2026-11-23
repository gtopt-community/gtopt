cmake_minimum_required(VERSION 3.14)

# run_cli_case.cmake - Run the gtopt binary with arbitrary CLI arguments and
# check its exit code, stdout/stderr and the files it leaves behind.
#
# Usage:
#   cmake -DGTOPT_BINARY=<path> -DWORK_DIR=<path>
#         -DCLI_ARGS=<|-list>           arguments passed to gtopt verbatim
#         [-DEXPECT_EXIT=<n>]           expected exit code (default 0)
#         [-DSTDOUT_REGEX=<regex>]      must match stdout
#         [-DSTDERR_REGEX=<regex>]      must match stderr
#         [-DNOT_STDOUT_REGEX=<regex>]  must NOT match stdout
#         [-DEXPECT_FILES=<|-list>]     paths (relative to WORK_DIR) that
#                                       must exist afterwards (globs OK)
#         [-DINPUT_NAME=<file> -DINPUT_CONTENT=<text>]
#                                       write a fixture file into WORK_DIR
#         -P run_cli_case.cmake
#
# Lists use '|' as separator (a ';' would be split by add_test()).
#
# WORK_DIR is wiped and recreated first, and gtopt runs with it as the
# current directory, so relative output paths land inside it.  The ARGS
# token `@WORK_DIR@` is replaced by the absolute work directory.

if(NOT EXISTS "${GTOPT_BINARY}")
  message(FATAL_ERROR "gtopt binary does not exist: ${GTOPT_BINARY}")
endif()
if(NOT DEFINED EXPECT_EXIT)
  set(EXPECT_EXIT 0)
endif()

if(EXISTS "${WORK_DIR}")
  file(REMOVE_RECURSE "${WORK_DIR}")
endif()
file(MAKE_DIRECTORY "${WORK_DIR}")

if(DEFINED INPUT_NAME)
  file(WRITE "${WORK_DIR}/${INPUT_NAME}" "${INPUT_CONTENT}")
endif()

string(REPLACE "|" ";" CLI_ARGS "${CLI_ARGS}")
string(REPLACE "|" ";" EXPECT_FILES "${EXPECT_FILES}")

set(_args "")
foreach(_a IN LISTS CLI_ARGS)
  string(REPLACE "@WORK_DIR@" "${WORK_DIR}" _a "${_a}")
  list(APPEND _args "${_a}")
endforeach()

execute_process(
  COMMAND "${GTOPT_BINARY}" ${_args}
  WORKING_DIRECTORY "${WORK_DIR}"
  RESULT_VARIABLE _exit
  OUTPUT_VARIABLE _stdout
  ERROR_VARIABLE _stderr
)

set(_fail "")
if(NOT "${_exit}" STREQUAL "${EXPECT_EXIT}")
  string(APPEND _fail "exit code ${_exit}, expected ${EXPECT_EXIT}\n")
endif()
if(DEFINED STDOUT_REGEX AND NOT _stdout MATCHES "${STDOUT_REGEX}")
  string(APPEND _fail "stdout does not match '${STDOUT_REGEX}'\n")
endif()
if(DEFINED NOT_STDOUT_REGEX AND _stdout MATCHES "${NOT_STDOUT_REGEX}")
  string(APPEND _fail "stdout unexpectedly matches '${NOT_STDOUT_REGEX}'\n")
endif()
if(DEFINED STDERR_REGEX AND NOT _stderr MATCHES "${STDERR_REGEX}")
  string(APPEND _fail "stderr does not match '${STDERR_REGEX}'\n")
endif()
foreach(_f IN LISTS EXPECT_FILES)
  file(GLOB _hits "${WORK_DIR}/${_f}")
  if(NOT _hits)
    string(APPEND _fail "expected file missing: ${_f}\n")
  endif()
endforeach()

if(_fail)
  string(SUBSTRING "${_stdout}" 0 4000 _stdout_head)
  string(SUBSTRING "${_stderr}" 0 4000 _stderr_head)
  message(FATAL_ERROR
    "gtopt ${_args}\n${_fail}"
    "--- stdout (head) ---\n${_stdout_head}\n"
    "--- stderr (head) ---\n${_stderr_head}")
endif()
message(STATUS "gtopt ${_args}: exit ${_exit} as expected")
