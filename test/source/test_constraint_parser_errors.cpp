/**
 * @file      test_constraint_parser_errors.cpp
 * @brief     ConstraintParser rejects malformed expressions with a message
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Each malformed expression must raise `ConstraintParseError` (never
 * parse into a silently different constraint) and the message must name
 * what was expected, so a PAMPL author can fix the line.  Covers the
 * error branches of the coefficient-profile, `sum{}` time-aggregation,
 * sum-filter predicate and if-condition sub-parsers.
 */

#include <string>
#include <string_view>

#include <doctest/doctest.h>
#include <gtopt/constraint_parser.hpp>

using namespace gtopt;

namespace
{

/// Parse @p expr expecting a ConstraintParseError whose message contains
/// @p needle.
void cpe_reject(std::string_view expr, std::string_view needle)
{
  CAPTURE(expr);
  try {
    (void)ConstraintParser::parse(std::string {expr});
    FAIL("expression parsed but should have been rejected");
  } catch (const ConstraintParseError& e) {
    const std::string msg = e.what();
    CAPTURE(msg);
    CHECK(msg.find(needle) != std::string::npos);
  }
}

}  // namespace

TEST_CASE(
    "ConstraintParser errors - primary and coefficient profile")  // NOLINT
{
  cpe_reject("generator('g').generation <= )", "expected number, '('");
  cpe_reject("[1, x] * generator('g').generation <= 1",
             "expected number in coefficient profile");
  cpe_reject("[1 2] * generator('g').generation <= 1",
             "expected ',' or ']' in coefficient profile");
}

TEST_CASE("ConstraintParser errors - sum{} time aggregation")  // NOLINT
{
  cpe_reject("sum{ 3 in stage } generator('g').generation <= 1",
             "expected an index name after `sum{`");
  cpe_reject("sum{ b of stage } generator('g').generation <= 1",
             "expected `in` after the index name");
  cpe_reject("sum{ b in 7 } generator('g').generation <= 1",
             "expected a time window");
  cpe_reject("sum{ b in stage } dur[3] * generator('g').generation <= 1",
             "expected the bound index inside `dur[...]`");
  cpe_reject("sum{ b in stage } <= 1", "expected number, '('");
}

TEST_CASE("ConstraintParser errors - sum filter predicates")  // NOLINT
{
  cpe_reject("sum(generator(all : 5 = 1).generation) <= 1",
             "expected filter attribute name");
  cpe_reject("sum(generator(all : type in { x }).generation) <= 1",
             "expected string or number in set");
  cpe_reject("sum(generator(all : type * 'a').generation) <= 1",
             "expected comparison operator");
  cpe_reject("sum(generator(all : type = (1)).generation) <= 1",
             "expected number or string on RHS of predicate");
}

TEST_CASE("ConstraintParser errors - if-conditions")  // NOLINT
{
  const auto body = std::string {" then (generator('g').generation) <= 1"};
  cpe_reject("if 3 = 1" + body, "expected coordinate name");
  cpe_reject("if season = 1" + body, "unknown if-condition coordinate");
  cpe_reject("if stage in { x }" + body, "expected integer in condition set");
  cpe_reject("if stage in { 1 .. x }" + body, "expected integer");
  cpe_reject("if stage * 1" + body, "expected comparison operator");
  cpe_reject("if stage = 'a'" + body, "expected integer literal");
  cpe_reject("if stage = 99999999999999999999" + body,
             "invalid integer literal");
}

TEST_CASE("ConstraintParser errors - token names in expect messages")  // NOLINT
{
  // `expect(...)` failures name the missing token kind.
  cpe_reject("generator('g'.generation <= 1", "expected");
  cpe_reject("sum(generator('g').generation <= 1", "expected");
  cpe_reject("generator('g').generation[1 <= 1", "expected");
  cpe_reject("sum(generator(all : type in { 'a' ).generation) <= 1",
             "expected");
}
