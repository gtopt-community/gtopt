/**
 * @file      test_long_input_index_st.cpp
 * @brief     (Scenario, Stage) long-layout index builder and uid columns
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `test_long_input_index.cpp` covers the STB / TB / T arities.  The
 * (Scenario, Stage) overload carries its own column discovery (either
 * axis may be absent) and its own error returns; this file pins them,
 * plus the multi-chunk and type-mismatch branches of
 * `UidColumn::make_uid_column` that every input reader goes through.
 * Helpers are prefixed `liist_` for unity builds.
 */

#include <cstdint>
#include <memory>
#include <stdexcept>
#include <string>
#include <tuple>
#include <vector>

#include <arrow/api.h>
#include <doctest/doctest.h>
#include <gtopt/uididx_traits.hpp>

using namespace gtopt;

namespace
{

using StIdx = UidToArrowIdx<ScenarioUid, StageUid>;
using StKey = std::tuple<ScenarioUid, StageUid>;

[[nodiscard]] auto liist_i32(const std::vector<int32_t>& v) -> ArrowArray
{
  arrow::Int32Builder b;
  REQUIRE(b.AppendValues(v).ok());
  std::shared_ptr<arrow::Array> a;
  REQUIRE(b.Finish(&a).ok());
  return a;
}

[[nodiscard]] auto liist_f64(const std::vector<double>& v) -> ArrowArray
{
  arrow::DoubleBuilder b;
  REQUIRE(b.AppendValues(v).ok());
  std::shared_ptr<arrow::Array> a;
  REQUIRE(b.Finish(&a).ok());
  return a;
}

[[nodiscard]] auto liist_str(const std::vector<std::string>& v) -> ArrowArray
{
  arrow::StringBuilder b;
  REQUIRE(b.AppendValues(v).ok());
  std::shared_ptr<arrow::Array> a;
  REQUIRE(b.Finish(&a).ok());
  return a;
}

}  // namespace

TEST_CASE("make_arrow_uids_idx_long ST — both axes")  // NOLINT
{
  auto t = arrow::Table::Make(arrow::schema({
                                  arrow::field("scenario", arrow::int32()),
                                  arrow::field("stage", arrow::int32()),
                                  arrow::field("uid", arrow::int32()),
                                  arrow::field("value", arrow::float64()),
                              }),
                              {
                                  liist_i32({1, 1, 2}),
                                  liist_i32({1, 2, 1}),
                                  liist_i32({5, 5, 6}),
                                  liist_f64({1.0, 2.0, 3.0}),
                              });

  auto [groups, mask] = StIdx::make_arrow_uids_idx_long(t);
  CHECK((mask & 0b11U) == 0b11U);
  CHECK((mask & kArrowSparseZeroFillBit) != 0);
  REQUIRE(groups.size() == 2);
  REQUIRE(groups.contains(5));
  REQUIRE(groups.contains(6));
  CHECK(groups.at(5)->size() == 2);
  CHECK(groups.at(5)->at(StKey {make_uid<Scenario>(1), make_uid<Stage>(2)})
        == 1);
  CHECK(groups.at(6)->at(StKey {make_uid<Scenario>(2), make_uid<Stage>(1)})
        == 2);
}

TEST_CASE("make_arrow_uids_idx_long ST — one axis absent")  // NOLINT
{
  SUBCASE("stage only: scenario key is the default uid")
  {
    auto t = arrow::Table::Make(arrow::schema({
                                    arrow::field("stage", arrow::int32()),
                                    arrow::field("uid", arrow::int32()),
                                    arrow::field("value", arrow::float64()),
                                }),
                                {
                                    liist_i32({1, 2}),
                                    liist_i32({4, 4}),
                                    liist_f64({1.0, 2.0}),
                                });
    auto [groups, mask] = StIdx::make_arrow_uids_idx_long(t);
    CHECK((mask & 0b01U) == 0U);
    CHECK((mask & 0b10U) != 0U);
    REQUIRE(groups.contains(4));
    CHECK(groups.at(4)->at(StKey {ScenarioUid {}, make_uid<Stage>(2)}) == 1);
  }

  SUBCASE("scenario only: stage key is the default uid")
  {
    auto t = arrow::Table::Make(arrow::schema({
                                    arrow::field("scenario", arrow::int32()),
                                    arrow::field("uid", arrow::int32()),
                                    arrow::field("value", arrow::float64()),
                                }),
                                {
                                    liist_i32({3}),
                                    liist_i32({4}),
                                    liist_f64({1.0}),
                                });
    auto [groups, mask] = StIdx::make_arrow_uids_idx_long(t);
    CHECK((mask & 0b01U) != 0U);
    CHECK((mask & 0b10U) == 0U);
    REQUIRE(groups.contains(4));
    CHECK(groups.at(4)->at(StKey {make_uid<Scenario>(3), StageUid {}}) == 0);
  }
}

TEST_CASE("make_arrow_uids_idx_long ST — rejected tables")  // NOLINT
{
  SUBCASE("neither scenario nor stage column")
  {
    auto t = arrow::Table::Make(arrow::schema({
                                    arrow::field("uid", arrow::int32()),
                                    arrow::field("value", arrow::float64()),
                                }),
                                {liist_i32({1}), liist_f64({1.0})});
    auto [groups, mask] = StIdx::make_arrow_uids_idx_long(t);
    CHECK(groups.empty());
    CHECK(mask == 0);
  }

  SUBCASE("missing uid column")
  {
    auto t = arrow::Table::Make(arrow::schema({
                                    arrow::field("stage", arrow::int32()),
                                    arrow::field("value", arrow::float64()),
                                }),
                                {liist_i32({1}), liist_f64({1.0})});
    auto [groups, mask] = StIdx::make_arrow_uids_idx_long(t);
    CHECK(groups.empty());
    CHECK(mask == 0);
  }

  SUBCASE("non-integer stage column")
  {
    auto t = arrow::Table::Make(arrow::schema({
                                    arrow::field("stage", arrow::utf8()),
                                    arrow::field("uid", arrow::int32()),
                                    arrow::field("value", arrow::float64()),
                                }),
                                {
                                    liist_str({"1"}),
                                    liist_i32({1}),
                                    liist_f64({1.0}),
                                });
    auto [groups, mask] = StIdx::make_arrow_uids_idx_long(t);
    CHECK(groups.empty());
    CHECK(mask == 0);
  }

  SUBCASE("non-integer scenario column")
  {
    auto t = arrow::Table::Make(arrow::schema({
                                    arrow::field("scenario", arrow::utf8()),
                                    arrow::field("uid", arrow::int32()),
                                    arrow::field("value", arrow::float64()),
                                }),
                                {
                                    liist_str({"1"}),
                                    liist_i32({1}),
                                    liist_f64({1.0}),
                                });
    auto [groups, mask] = StIdx::make_arrow_uids_idx_long(t);
    CHECK(groups.empty());
    CHECK(mask == 0);
  }

  SUBCASE("duplicate (scenario, stage) for one uid throws")
  {
    auto t = arrow::Table::Make(arrow::schema({
                                    arrow::field("scenario", arrow::int32()),
                                    arrow::field("stage", arrow::int32()),
                                    arrow::field("uid", arrow::int32()),
                                    arrow::field("value", arrow::float64()),
                                }),
                                {
                                    liist_i32({1, 1}),
                                    liist_i32({1, 1}),
                                    liist_i32({7, 7}),
                                    liist_f64({1.0, 2.0}),
                                });
    CHECK_THROWS_AS(StIdx::make_arrow_uids_idx_long(t), std::runtime_error);
  }
}

TEST_CASE("make_uid_column — chunking and type checks")  // NOLINT
{
  SUBCASE("multi-chunk column is concatenated in row order")
  {
    auto chunked = std::make_shared<arrow::ChunkedArray>(arrow::ArrayVector {
        liist_i32({1, 2}),
        liist_i32({3}),
    });
    auto t = arrow::Table::Make(
        arrow::schema({arrow::field("stage", arrow::int32())}), {chunked});
    auto col = UidColumn::make_uid_column(t, "stage");
    REQUIRE(col.has_value());
    const auto& arr = col.value();
    REQUIRE(arr->length() == 3);
    CHECK(arr->Value(0) == 1);
    CHECK(arr->Value(2) == 3);
  }

  SUBCASE("column without chunks")
  {
    auto empty = std::make_shared<arrow::ChunkedArray>(arrow::ArrayVector {},
                                                       arrow::int32());
    auto t = arrow::Table::Make(
        arrow::schema({arrow::field("stage", arrow::int32())}), {empty});
    CHECK_FALSE(UidColumn::make_uid_column(t, "stage").has_value());
  }

  SUBCASE("floating-point column is a type mismatch")
  {
    auto t = arrow::Table::Make(
        arrow::schema({arrow::field("stage", arrow::float64())}),
        {liist_f64({1.0})});
    const auto col = UidColumn::make_uid_column(t, "stage");
    REQUIRE_FALSE(col.has_value());
    CHECK(col.error().find("Type mismatch") != std::string::npos);
  }

  SUBCASE("missing column")
  {
    auto t = arrow::Table::Make(
        arrow::schema({arrow::field("stage", arrow::int32())}),
        {liist_i32({1})});
    CHECK_FALSE(UidColumn::make_uid_column(t, "block").has_value());
  }
}
